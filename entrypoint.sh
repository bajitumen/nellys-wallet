#!/usr/bin/env bash
set -euo pipefail

DB_PATH="/var/data/finance.db"

# Render overlays /var/data after the Docker chown. Files left from older
# root-owned deploys break litestream WAL cleanup; chown then drop via gosu.
if [[ "$(id -u)" -eq 0 ]]; then
  mkdir -p "$(dirname "$DB_PATH")"
  chown -R app:app /var/data
  exec gosu app "$0" "$@"
fi

mkdir -p "$(dirname "$DB_PATH")"

db_needs_restore() {
  # Missing / empty / corrupt — a plain `! -f` check would boot on broken data.
  [[ ! -s "$DB_PATH" ]] && return 0
  if command -v sqlite3 >/dev/null 2>&1; then
    sqlite3 "$DB_PATH" "PRAGMA integrity_check;" 2>/dev/null \
      | grep -q '^ok$' || return 0
  fi
  return 1
}

if [[ -n "${LITESTREAM_REPLICA_URL:-}" ]]; then
  if db_needs_restore; then
    echo "Restoring SQLite database from $LITESTREAM_REPLICA_URL"
    # Sideline -wal/-shm too; leaving them lets SQLite replay corruption
    # into the restored DB on open. Keep for forensics.
    ts=$(date +%s)
    for ext in "" "-wal" "-shm"; do
      if [[ -e "${DB_PATH}${ext}" ]]; then
        mv "${DB_PATH}${ext}" "${DB_PATH}.broken.${ts}${ext}" 2>/dev/null \
          || rm -f "${DB_PATH}${ext}"
      fi
    done
    # Cap forensic triplets at 3 — a crash loop must not fill the 1GB disk.
    ls -1tr "${DB_PATH}".broken.* 2>/dev/null | head -n -9 | xargs -r rm -f
    # -if-replica-exists is 0 on legit first run; non-zero means restore
    # failed against an existing replica. Bail rather than ship an empty DB.
    if ! litestream restore -if-replica-exists -config /app/litestream.yml "$DB_PATH"; then
      echo "FATAL: litestream restore failed. Check credentials, network, and replica integrity." >&2
      exit 1
    fi
    if [[ ! -s "$DB_PATH" ]]; then
      echo "No existing replica found — starting with empty DB."
    fi
  fi
fi

# -w 1 is mandatory: in-process caches/locks are not shared across workers.
GUNICORN_THREADS="${GUNICORN_THREADS:-8}"
# --graceful-timeout 25 fits inside Render's 30s SIGTERM grace.
GUNICORN_CMD="gunicorn -w 1 --threads ${GUNICORN_THREADS} --timeout 90 --graceful-timeout 25 -b 0.0.0.0:${PORT:-5001} --chdir /app/code app:app"

# Run migrations and serving under one process so init-db's WAL frames
# replicate too — a crash between migrate and first checkpoint would
# otherwise restore a pre-migration schema.
APP_CMD="python /app/code/cli.py init-db && ${GUNICORN_CMD}"

if [[ -n "${LITESTREAM_REPLICA_URL:-}" ]]; then
  # Background probe: alert loud if no successful generation appears after
  # 60s. Catches misconfigured S3 creds where writes succeed but backups
  # silently never run — restore would then yield an empty DB.
  (
    sleep 60
    if ! litestream snapshots -config /app/litestream.yml "$DB_PATH" 2>/dev/null \
        | grep -q .; then
      echo "ALERT: litestream has no snapshots 60s after start — check S3 creds + replica URL." >&2
    fi
  ) &
  exec litestream replicate -config /app/litestream.yml -exec "bash -c '${APP_CMD}'"
else
  echo "LITESTREAM_REPLICA_URL not set — running without backups."
  bash -c "${APP_CMD}"
fi
