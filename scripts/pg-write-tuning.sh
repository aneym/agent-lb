#!/bin/bash
set -euo pipefail
PSQL=${PSQL:-/opt/homebrew/opt/postgresql@17/bin/psql}
PGHOST=${PGHOST:-127.0.0.1}
SETTINGS=(
  "work_mem=32MB"
  "wal_compression=lz4"
  "checkpoint_timeout=15min"
  "max_wal_size=4GB"
)
q() { "$PSQL" -h "$PGHOST" -d postgres -XAtqc "$1"; }
if [ "${1:-}" != "--check" ]; then
  for kv in "${SETTINGS[@]}"; do
    q "ALTER SYSTEM SET ${kv%%=*} = '${kv#*=}'"
  done
  q "ALTER SYSTEM RESET log_temp_files"
  q "SELECT pg_reload_conf()" >/dev/null
  sleep 1
fi
status=0
for kv in "${SETTINGS[@]}"; do
  live=$(q "SHOW ${kv%%=*}")
  if [ "$live" = "${kv#*=}" ]; then echo "ok ${kv%%=*}=$live"; else echo "drift ${kv%%=*}=$live want ${kv#*=}"; status=1; fi
done
exit $status
