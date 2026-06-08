# syntax=docker/dockerfile:1
#
# Production image for Nelly's Wallet.
# - Python 3.13 slim base
# - gunicorn serves Flask
# - litestream replicates SQLite to S3-compatible storage (Backblaze B2,
#   Cloudflare R2, AWS S3) and restores from there on startup
# - Runs as unprivileged uid 10001 so RCE doesn't get root
#
# Required runtime env vars (besides the .env vars the app already reads):
#   LITESTREAM_REPLICA_URL  e.g. b2://my-bucket/nellys-wallet  (optional;
#                           skip the replication step if unset)
#   LITESTREAM_ACCESS_KEY   provider access key
#   LITESTREAM_SECRET_KEY   provider secret key

FROM python:3.13-slim AS base

ARG LITESTREAM_VERSION=0.3.13
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && curl -fsSL "https://github.com/benbjohnson/litestream/releases/download/v${LITESTREAM_VERSION}/litestream-v${LITESTREAM_VERSION}-linux-amd64.deb" \
       -o /tmp/litestream.deb \
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

RUN useradd -m -u 10001 -s /usr/sbin/nologin app \
    && mkdir -p /var/data \
    && chown -R app:app /app /var/data

ENV DATABASE_URL=sqlite:///var/data/finance.db
ENV FLASK_ENV=production
ENV PATH="/app/.venv/bin:$PATH"

USER app

EXPOSE 5001

CMD ["./entrypoint.sh"]
