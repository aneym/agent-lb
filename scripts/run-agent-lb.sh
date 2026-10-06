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
  local hostport host port
  hostport="${url#*://}"; hostport="${hostport#*@}"; hostport="${hostport%%/*}"
  host="${hostport%%:*}"; port="${hostport##*:}"
  [[ "$port" == "$hostport" || -z "$port" ]] && port=5432
  [[ -z "$host" ]] && host=127.0.0.1
  local pgready=""
  for c in /opt/homebrew/bin/pg_isready /opt/homebrew/opt/postgresql@17/bin/pg_isready; do
    [[ -x "$c" ]] && { pgready="$c"; break; }
  done
  local deadline=$(( $(date +%s) + ${AGENT_LB_DB_WAIT_SECONDS:-900} ))
  local delay=1 waited=0 start rc
  start=$(date +%s)
  while :; do
    if [[ -n "$pgready" ]]; then
      "$pgready" -q -h "$host" -p "$port" -t 3 >/dev/null 2>&1 && rc=0 || rc=$?
    else
      (exec 3<>"/dev/tcp/$host/$port") 2>/dev/null && rc=0 || rc=2
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
    sleep "$delay"
    (( delay < 10 )) && delay=$(( delay * 2 ))
  done
}
wait_for_postgres

exec .venv/bin/agent-lb "$@"
