#!/bin/bash
# Canonical source for ~/.agent-lb/bin/backup.sh (run daily by launchd).
# Daily backup of the live agent-lb Postgres DB (the historical usage record:
# request_logs / usage_history / additional_usage_history / accounts). Keeps the
# last 14 dumps. The server is Postgres 17, so we must use the v17 pg_dump.
# Overrides: PG_DUMP_BIN, AGENT_LB_DATABASE_URL (Postgres URLs only; the default
# carries no password, so libpq reads it from PGPASSWORD or ~/.pgpass),
# AGENT_LB_BACKUP_DIR, AGENT_LB_BACKUP_KEEP.
# Deletes only its own agent_lb-*.sql.gz files, and only inside the backup dir.
set -uo pipefail

PG_DUMP="${PG_DUMP_BIN:-/opt/homebrew/opt/postgresql@17/bin/pg_dump}"
DSN="${AGENT_LB_DATABASE_URL:-}"
DSN="${DSN/postgresql+asyncpg:\/\//postgresql://}"
case "$DSN" in
  postgresql://*) ;;
  *) DSN="postgresql://agent_lb@127.0.0.1:5432/agent_lb" ;;
esac
DIR="${AGENT_LB_BACKUP_DIR:-$HOME/.agent-lb/backups}"
KEEP="${AGENT_LB_BACKUP_KEEP:-14}"

# Refuse an empty, relative, root or dot-segment backup dir, and a non-numeric
# or zero keep count, before anything can be removed.
case "${DIR:?backup dir is empty}" in
  /) echo "refusing backup dir /" >&2; exit 2 ;;
  /*) ;;
  *) echo "refusing relative backup dir" >&2; exit 2 ;;
esac
case "/$DIR/" in
  */../*|*/./*) echo "refusing backup dir with . or .. segments" >&2; exit 2 ;;
esac
case "$KEEP" in
  ''|*[!0-9]*|0) echo "AGENT_LB_BACKUP_KEEP must be a positive integer" >&2; exit 2 ;;
esac
DIR="${DIR%/}"

mkdir -p "$DIR"
ts="$(date +%Y%m%d-%H%M%S)"
out="$DIR/agent_lb-$ts.sql.gz"
log="$DIR/backup.log"

# Remove one file only if it is a dump this script wrote, directly inside $DIR.
remove_dump() {
  local f="${1:?remove_dump needs a path}"
  case "$f" in
    "${DIR:?}"/agent_lb-*.sql.gz) ;;
    *) echo "$(date -u +%FT%TZ) ERROR: refusing to remove path outside the backup dir" >> "$log"; return 1 ;;
  esac
  [[ "$(dirname -- "$f")" == "$DIR" ]] || return 1
  rm -f -- "$f"
}

if [[ ! -x "$PG_DUMP" ]]; then
  echo "$(date -u +%FT%TZ) ERROR: $PG_DUMP not found" >> "$log"
  exit 1
fi

if "$PG_DUMP" "$DSN" 2>>"$log" | gzip > "$out"; then
  # sanity: a real dump is well over 1MB; refuse to keep an empty/failed one
  size=$(stat -f%z "$out" 2>/dev/null || echo 0)
  if [[ "$size" -lt 1000000 ]]; then
    echo "$(date -u +%FT%TZ) ERROR: dump too small ($size B), removing $out" >> "$log"
    remove_dump "$out"
    exit 1
  fi
  echo "$(date -u +%FT%TZ) ok $out ($(du -h "$out" | cut -f1))" >> "$log"
  # rotate: keep newest $KEEP
  ls -t "$DIR"/agent_lb-*.sql.gz 2>/dev/null | tail -n +$((KEEP + 1)) | while IFS= read -r old; do
    remove_dump "$old"
  done
else
  echo "$(date -u +%FT%TZ) ERROR: pg_dump failed" >> "$log"
  remove_dump "$out"
  exit 1
fi
