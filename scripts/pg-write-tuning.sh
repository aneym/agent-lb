#!/bin/bash
set -euo pipefail
PSQL=${PSQL:-/opt/homebrew/opt/postgresql@17/bin/psql}
PG_ISREADY=${PG_ISREADY:-/opt/homebrew/opt/postgresql@17/bin/pg_isready}
PG_CTL=${PG_CTL:-/opt/homebrew/opt/postgresql@17/bin/pg_ctl}
PGHOST=${PGHOST:-127.0.0.1}
PG_LABEL=${PG_LABEL:-sh.brew.postgresql@17}
SETTINGS=(
  "work_mem=32MB"
  "wal_compression=lz4"
  "checkpoint_timeout=15min"
  "max_wal_size=4GB"
  "shared_buffers=2GB"
)
q() { "$PSQL" -h "$PGHOST" -d postgres -XAtqc "$1"; }
mode=${1:-apply}
if [ "$mode" != "--check" ]; then
  for kv in "${SETTINGS[@]}"; do
    q "ALTER SYSTEM SET ${kv%%=*} = '${kv#*=}'"
  done
  q "ALTER SYSTEM RESET log_temp_files"
  q "SELECT pg_reload_conf()" >/dev/null
  sleep 1
fi
if [ "$mode" = "--restart" ] && [ "$(q "SELECT count(*) FROM pg_settings WHERE pending_restart")" != "0" ]; then
  datadir=$(q "SHOW data_directory")
  "$PG_CTL" -D "$datadir" stop -m fast -w -t 60
  launchctl kickstart "gui/$(id -u)/$PG_LABEL" || true
  for _ in $(seq 1 120); do
    "$PG_ISREADY" -h "$PGHOST" -q -t 5 && break
    sleep 2
  done
fi
status=0
for kv in "${SETTINGS[@]}"; do
  live=$(q "SHOW ${kv%%=*}")
  if [ "$live" = "${kv#*=}" ]; then echo "ok ${kv%%=*}=$live"; else echo "drift ${kv%%=*}=$live want ${kv#*=}"; status=1; fi
done
exit $status
