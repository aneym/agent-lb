#!/bin/bash
# Start the approved internal runtime. Deployment is a separate coordinated step:
# run the configured sync-runtime.sh interactively, verify candidate hashes,
# then use the normal launchctl kickstart during an approved service window.
#
# Database readiness gate (2026-10-06): after an unclean reboot PostgreSQL spends
# minutes in fsync + crash recovery ("the database system is starting up").
# agent-lb runs migrations at startup and exits on that error, so launchd
# crash-looped it 14 times and the watchdog kicked it mid-recovery. Wait here,
# bounded, until the configured Postgres accepts connections, then exec.
# On timeout we exec anyway so the app's own error reaches the log and launchd
# retries; we never hang forever.
set -e
cd "$HOME/.agent-lb/runtime/agent-lb"

wait_for_postgres() {
  local url="${AGENT_LB_DATABASE_URL:-}"
  case "$url" in
    postgresql*://*) ;;
    *) return 0 ;;
  esac
  local endpoint host port
  if ! endpoint=$(python3 - 2>/dev/null <<'PYURL'
import os
import re
from urllib.parse import urlsplit

url = os.environ.get("AGENT_LB_DATABASE_URL", "")
try:
    scheme, rest = url.split("://", 1)
    # Raw delimiters in SQLAlchemy userinfo must not truncate the authority.
    endpoint = urlsplit(scheme + "://" + rest.rsplit("@", 1)[-1])
    host = endpoint.hostname or "127.0.0.1"
    port = endpoint.port or 5432
    if not re.fullmatch(r"[A-Za-z0-9_.:%-]+", host):
        raise ValueError("invalid host")
    print(host, port)
except (ValueError, TypeError):
    raise SystemExit(1)
PYURL
  ); then
    echo "db: unparseable url" >&2
    return 0
  fi
  read -r host port <<<"$endpoint"
  local pgready=""
  for c in /opt/homebrew/bin/pg_isready /opt/homebrew/opt/postgresql@17/bin/pg_isready; do
    [[ -x "$c" ]] && { pgready="$c"; break; }
  done
  [[ -n "$pgready" ]] || pgready=$(command -v pg_isready || true)
  local timeout_bin=""
  timeout_bin=$(command -v timeout || command -v gtimeout || true)
  local deadline=$(( $(date +%s) + ${AGENT_LB_DB_WAIT_SECONDS:-900} ))
  local delay=1 waited=0 start rc remaining probe_timeout sleep_seconds
  start=$(date +%s)
  while :; do
    remaining=$(( deadline - $(date +%s) ))
    (( remaining > 0 )) || break
    probe_timeout=2
    (( remaining < probe_timeout )) && probe_timeout=$remaining
    if [[ -n "$pgready" ]]; then
      if [[ -n "$timeout_bin" ]]; then
        "$timeout_bin" "$remaining" "$pgready" -q -h "$host" -p "$port" -t "$probe_timeout" >/dev/null 2>&1 && rc=0 || rc=$?
      else
        # macOS without coreutils: terminate even a DNS-stalled probe.
        "$pgready" -q -h "$host" -p "$port" -t "$probe_timeout" >/dev/null 2>&1 &
        local probe_pid=$! timer_pid
        (
          sleep "$remaining" &
          sleeper=$!
          trap 'kill "$sleeper" 2>/dev/null || true' TERM
          wait "$sleeper" && kill -KILL "$probe_pid" 2>/dev/null
        ) &
        timer_pid=$!
        wait "$probe_pid" 2>/dev/null && rc=0 || rc=$?
        kill "$timer_pid" 2>/dev/null || true
        wait "$timer_pid" 2>/dev/null || true
      fi
    else
      python3 - "$host" "$port" "$probe_timeout" >/dev/null 2>&1 <<'PYPROBE' && rc=0 || rc=2
import signal
import socket
import sys

signal.alarm(int(sys.argv[3]))
with socket.create_connection((sys.argv[1], int(sys.argv[2])), timeout=float(sys.argv[3])):
    pass
PYPROBE
    fi
    if (( rc == 0 )); then
      (( waited )) && echo "$(date -u +%FT%TZ) run-agent-lb: postgres $host:$port ready after $(( $(date +%s) - start ))s" >&2
      return 0
    fi
    if (( $(date +%s) >= deadline )); then
      echo "$(date -u +%FT%TZ) run-agent-lb: postgres $host:$port not ready after $(( $(date +%s) - start ))s (pg_isready rc=$rc); starting anyway" >&2
      return 0
    fi
    if (( waited == 0 )); then
      echo "$(date -u +%FT%TZ) run-agent-lb: waiting for postgres $host:$port (pg_isready rc=$rc)" >&2
    fi
    waited=1
    remaining=$(( deadline - $(date +%s) ))
    sleep_seconds=$delay
    (( remaining < sleep_seconds )) && sleep_seconds=$remaining
    (( sleep_seconds > 0 )) && sleep "$sleep_seconds"
    if (( delay < 8 )); then
      delay=$(( delay * 2 ))
    fi
  done
  echo "$(date -u +%FT%TZ) run-agent-lb: postgres $host:$port not ready after $(( $(date +%s) - start ))s; starting anyway" >&2
}
wait_for_postgres

exec .venv/bin/agent-lb "$@"
