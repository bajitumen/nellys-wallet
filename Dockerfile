# syntax=docker/dockerfile:1
#
# Production image for Nelly's Wallet.
# - Stage 1 (client): Node builds the React SPA into client/dist.
# - Stage 2 (base):   Python 3.13 slim runtime serving Flask + the built SPA.
# - gunicorn serves Flask
# - litestream replicates SQLite to S3-compatible storage (Backblaze B2,
#   Cloudflare R2, AWS S3) and restores from there on startup
# - Runs as unprivileged uid 10001 so RCE doesn't get root
#
# Required runtime env vars (besides the .env vars the app already reads):
#   LITESTREAM_REPLICA_URL        e.g. s3://my-bucket/nellys-wallet  (optional;
#                                 skip the replication step if unset)
#   LITESTREAM_ACCESS_KEY_ID      provider access key id
#   LITESTREAM_SECRET_ACCESS_KEY  provider secret access key
# Names must match render.yaml + litestream.yml verbatim; mismatch silently
# breaks backups (writes succeed, replicate auth fails, restore is empty).

# --- Stage 1: build the React SPA ------------------------------------------
FROM node:22-alpine AS client

WORKDIR /client
COPY client/package.json client/package-lock.json ./
RUN npm ci

COPY client/ ./
# Surface the Clerk publishable key at build time so Vite can embed it; the
# rest of Clerk's auth happens server-side and reads runtime env.
ARG VITE_CLERK_PUBLISHABLE_KEY=""
ENV VITE_CLERK_PUBLISHABLE_KEY=${VITE_CLERK_PUBLISHABLE_KEY}
RUN npm run build

# --- Stage 2: Python runtime -----------------------------------------------
FROM python:3.13-slim AS base

ARG LITESTREAM_VERSION=0.3.13
ARG LITESTREAM_SHA256=9b05043523c1fb1c4f9800623adf0015683da7fdd55e19b9fe5d28f63fae96b4
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates sqlite3 gosu \
    && curl -fsSL "https://github.com/benbjohnson/litestream/releases/download/v${LITESTREAM_VERSION}/litestream-v${LITESTREAM_VERSION}-linux-amd64.deb" \
       -o /tmp/litestream.deb \
    && echo "${LITESTREAM_SHA256}  /tmp/litestream.deb" | sha256sum -c - \
    && dpkg -i /tmp/litestream.deb \
    && rm /tmp/litestream.deb \
    && apt-get purge -y --auto-remove curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --extra prod

COPY code/ ./code/
COPY litestream.yml ./litestream.yml
COPY entrypoint.sh ./entrypoint.sh
RUN chmod +x ./entrypoint.sh

# Built SPA — without this layer Flask's SPA shell route 404s every page.
COPY --from=client /client/dist ./client/dist

RUN useradd -m -u 10001 -s /usr/sbin/nologin app \
    && mkdir -p /var/data \
    && chown -R app:app /app /var/data

ENV DATABASE_URL=sqlite:///var/data/finance.db
ENV FLASK_ENV=production
ENV PATH="/app/.venv/bin:$PATH"

# Container starts as root so entrypoint.sh can chown the runtime-mounted
# persistent disk (Render's disk mount overlays /var/data at boot, so the
# build-time chown above does NOT cover files that already exist on the
# disk from previous deploys). entrypoint.sh then drops to uid 10001 via
# gosu before any app process runs.
EXPOSE 5001

CMD ["./entrypoint.sh"]
