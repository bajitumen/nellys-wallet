#!/usr/bin/env bash
# Container entrypoint:
#   1. Ensure the persistent-disk directory exists.
#   2. If LITESTREAM_REPLICA_URL is set, try to restore the latest backup
#      into the empty DB before starting (this is what gives us continuity
#      across container restarts on hosts that wipe ephemeral storage).
#   3. Run gunicorn under `litestream replicate` so every write streams to
#      the replica in real time. Skip litestream entirely when no replica
#      URL is configured — useful for local Docker testing.
set -euo pipefail

DB_PATH="/var/data/finance.db"
mkdir -p "$(dirname "$DB_PATH")"

if [[ -n "${LITESTREAM_REPLICA_URL:-}" ]]; then
  if [[ ! -f "$DB_PATH" ]]; then
    echo "Restoring SQLite database from $LITESTREAM_REPLICA_URL"
    # -if-replica-exists exits 0 when no replica is found (legitimate first run);
    # any non-zero exit means the replica IS there but restore failed (bad creds,
    # corrupt snapshot, network error). Bail rather than overwrite the backup
    # with an empty DB.
    if ! litestream restore -if-replica-exists -config /app/litestream.yml "$DB_PATH"; then
      echo "FATAL: litestream restore failed. Check credentials, network, and replica integrity." >&2
      exit 1
    fi
    if [[ ! -f "$DB_PATH" ]]; then
      echo "No existing replica found — starting with empty DB."
    fi
  fi
fi

# Run schema migrations exactly once before workers boot. Doing this at app
# import time raced under multi-worker boot — concurrent ALTERs against the
# same SQLite file would crash one worker mid-migration.
echo "Initializing DB schema..."
python /app/code/cli.py init-db

# One process + threads is the only correct shape for this image: the in-process
# rule/spending caches and the per-key locks live in Python memory; -w N
# would let one worker invalidate while another keeps serving stale rows.
GUNICORN_THREADS="${GUNICORN_THREADS:-8}"
GUNICORN_CMD="gunicorn -w 1 --threads ${GUNICORN_THREADS} -b 0.0.0.0:${PORT:-5001} --chdir /app/code app:app"

if [[ -n "${LITESTREAM_REPLICA_URL:-}" ]]; then
  exec litestream replicate -config /app/litestream.yml -exec "${GUNICORN_CMD}"
else
  echo "LITESTREAM_REPLICA_URL not set — running without backups."
  exec ${GUNICORN_CMD}
fi
