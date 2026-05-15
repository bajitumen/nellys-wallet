# syntax=docker/dockerfile:1
#
# Production image for Nelly's Wallet.
# - Python 3.11 slim base
# - gunicorn serves Flask
# - litestream replicates SQLite to S3-compatible storage (Backblaze B2,
#   Cloudflare R2, AWS S3) and restores from there on startup
#
# Required runtime env vars (besides the .env vars the app already reads):
#   LITESTREAM_REPLICA_URL  e.g. b2://my-bucket/nellys-wallet  (optional;
#                           skip the replication step if unset)
#   LITESTREAM_ACCESS_KEY   provider access key
#   LITESTREAM_SECRET_KEY   provider secret key

FROM python:3.11-slim AS base

# Install litestream from the official release. Pin a specific version
# so a fresh build doesn't silently change what's deployed.
ARG LITESTREAM_VERSION=0.3.13
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && curl -fsSL "https://github.com/benbjohnson/litestream/releases/download/v${LITESTREAM_VERSION}/litestream-v${LITESTREAM_VERSION}-linux-amd64.deb" \
       -o /tmp/litestream.deb \
    && dpkg -i /tmp/litestream.deb \
    && rm /tmp/litestream.deb \
    && apt-get purge -y --auto-remove curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -e ".[prod]"

COPY code/ ./code/
COPY litestream.yml ./litestream.yml
COPY entrypoint.sh ./entrypoint.sh
RUN chmod +x ./entrypoint.sh

# Render mounts a persistent disk at /var/data; the app uses ./instance for
# DATABASE_URL by default. The entrypoint links them so a fresh container
# starts with the previous container's DB.
ENV DATABASE_URL=sqlite:///var/data/finance.db
ENV FLASK_ENV=production

EXPOSE 5001

CMD ["./entrypoint.sh"]
