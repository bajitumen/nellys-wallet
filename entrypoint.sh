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

# Render mounts the persistent disk at /var/data at runtime, overlaying
# whatever was chowned in the Docker build. Files that already exist on
# the disk (from previous deploys when the container ran as root) keep
# their old ownership, and the unprivileged app user then can't delete
# them — surfaces as a loud "permission denied" on every litestream WAL
# cleanup. Fix it once at startup, then drop privileges via gosu.
if [[ "$(id -u)" -eq 0 ]]; then
  mkdir -p "$(dirname "$DB_PATH")"
  chown -R app:app /var/data
  exec gosu app "$0" "$@"
fi

mkdir -p "$(dirname "$DB_PATH")"

db_needs_restore() {
  # Restore when the file is missing, zero bytes, or fails the SQLite integrity
  # check. The plain `! -f` guard let a corrupt-but-present file boot the app
  # on broken data instead of recovering from the replica.
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
    # Move the broken file AND its WAL/SHM sidecars aside. WAL/SHM hold
    # committed-but-not-checkpointed frames; if left in place, SQLite would
    # replay them into the freshly restored DB on first open and silently
    # re-inject the corruption we just escaped (which litestream then ships
    # back to the bucket). Keep them for forensic recovery.
    ts=$(date +%s)
    for ext in "" "-wal" "-shm"; do
      if [[ -e "${DB_PATH}${ext}" ]]; then
        mv "${DB_PATH}${ext}" "${DB_PATH}.broken.${ts}${ext}" 2>/dev/null \
          || rm -f "${DB_PATH}${ext}"
      fi
    done
    # Cap forensic copies at the 3 most recent triplets so a crash loop
    # against a corrupt DB can't fill the 1GB persistent disk.
    ls -1tr "${DB_PATH}".broken.* 2>/dev/null | head -n -9 | xargs -r rm -f
    # -if-replica-exists exits 0 when no replica is found (legitimate first run);
    # any non-zero exit means the replica IS there but restore failed (bad creds,
    # corrupt snapshot, network error). Bail rather than overwrite the backup
    # with an empty DB.
    if ! litestream restore -if-replica-exists -config /app/litestream.yml "$DB_PATH"; then
      echo "FATAL: litestream restore failed. Check credentials, network, and replica integrity." >&2
      exit 1
    fi
    if [[ ! -s "$DB_PATH" ]]; then
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
# --graceful-timeout 60 outpaces Render's default 30s SIGTERM-to-SIGKILL grace
# (Render gives services 30s before force-killing on deploy). Workers finish
# in-flight writes instead of being killed mid-commit. Worker --timeout 90 so
# the cold-cache /api/overview can complete without being recycled.
GUNICORN_CMD="gunicorn -w 1 --threads ${GUNICORN_THREADS} --timeout 90 --graceful-timeout 25 -b 0.0.0.0:${PORT:-5001} --chdir /app/code app:app"

if [[ -n "${LITESTREAM_REPLICA_URL:-}" ]]; then
  exec litestream replicate -config /app/litestream.yml -exec "${GUNICORN_CMD}"
else
  echo "LITESTREAM_REPLICA_URL not set — running without backups."
  exec ${GUNICORN_CMD}
fi
