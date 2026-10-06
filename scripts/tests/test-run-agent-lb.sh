#!/usr/bin/env bash
set -euo pipefail

# Subprocess integration: boot logging and deadlines at the launcher boundary.
# The old delimiter-first parser leaked userinfo; an unbounded pg_isready
# could ignore the wait budget. No existing shell regression covers boot.
root=$(cd "$(dirname "$0")/../.." && pwd)
tmp=$(mktemp -d "${TMPDIR:-/tmp}/agent-lb-boot-test.XXXXXX")
trap 'rm -rf "${tmp:?}"' EXIT
mkdir -p "$tmp/home/.agent-lb/runtime/agent-lb/.venv/bin" "$tmp/bin"
cat > "$tmp/home/.agent-lb/runtime/agent-lb/.venv/bin/agent-lb" <<'STUB'
#!/bin/bash
echo runtime-started
STUB
chmod +x "$tmp/home/.agent-lb/runtime/agent-lb/.venv/bin/agent-lb"
# Credentials are synthetic regression fixtures, not account data.
url='postgresql+asyncpg://fixture-user:fixture-prefix%2F%3F%23fixture-tail@127.0.0.1:1/db?password=fixture-query@fixture-query-host'
HOME="$tmp/home" AGENT_LB_DATABASE_URL="$url" AGENT_LB_DB_WAIT_SECONDS=1 \
  bash "$root/scripts/run-agent-lb.sh" >"$tmp/log" 2>&1
for forbidden in fixture-user fixture-prefix fixture-tail fixture-query fixture-query-host password=; do
  if grep -Fq "$forbidden" "$tmp/log"; then
    echo 'FAIL: database credential appeared in boot log' >&2
    exit 1
  fi
done
grep -Fq 'postgres 127.0.0.1:1' "$tmp/log"
grep -q runtime-started "$tmp/log"
echo 'PASS: boot logs contain only the database endpoint'

start=$(date +%s)
HOME="$tmp/home" AGENT_LB_DATABASE_URL='postgresql://127.0.0.1:1/db' AGENT_LB_DB_WAIT_SECONDS=3 \
  bash "$root/scripts/run-agent-lb.sh" >"$tmp/log" 2>&1
elapsed=$(( $(date +%s) - start ))
[[ "$elapsed" -lt 6 ]]
grep -q runtime-started "$tmp/log"
echo "PASS: closed-port boot completed in ${elapsed}s (budget 3s, limit 6s)"

# Exercise the external readiness tool's DNS-stall boundary when no installed
# Homebrew probe takes priority. The launcher still runs unchanged.
if [[ ! -x /opt/homebrew/bin/pg_isready && ! -x /opt/homebrew/opt/postgresql@17/bin/pg_isready ]]; then
  cat > "$tmp/bin/pg_isready" <<'PROBE'
#!/bin/bash
exec sleep 30
PROBE
  chmod +x "$tmp/bin/pg_isready"
  start=$(date +%s)
  HOME="$tmp/home" PATH="$tmp/bin:$PATH" AGENT_LB_DATABASE_URL='postgresql://127.0.0.1:1/db' AGENT_LB_DB_WAIT_SECONDS=3 \
    bash "$root/scripts/run-agent-lb.sh" >"$tmp/log" 2>&1
  elapsed=$(( $(date +%s) - start ))
  [[ "$elapsed" -lt 6 ]]
  grep -q runtime-started "$tmp/log"
  echo "PASS: stalled readiness tool completed in ${elapsed}s (limit 6s)"
else
  echo 'SKIP: stalled readiness stub (installed Homebrew probe takes priority)'
fi

# Verify the real probe reaches the authority, not a query's trailing @ value.
python3 - "$tmp/port" "$tmp/connection" <<'SERVER' &
import socket
import sys
from pathlib import Path

with socket.socket() as server:
    server.bind(("127.0.0.1", 0))
    server.listen()
    server.settimeout(10)
    Path(sys.argv[1]).write_text(str(server.getsockname()[1]))
    connection, _ = server.accept()
    with connection:
        Path(sys.argv[2]).write_text("connected")
SERVER
server_pid=$!
for ((attempt = 0; attempt < 100; attempt++)); do
  [[ -s "$tmp/port" ]] && break
  sleep 0.05
done
port=$(cat "$tmp/port")
HOME="$tmp/home" AGENT_LB_DATABASE_URL="postgresql://fixture-user:fixture-secret@127.0.0.1:$port/db?password=fixture-query@fixture-query-host" AGENT_LB_DB_WAIT_SECONDS=1 \
  bash "$root/scripts/run-agent-lb.sh" >"$tmp/log" 2>&1
wait "$server_pid"
[[ -s "$tmp/connection" ]]
for forbidden in fixture-user fixture-secret fixture-query fixture-query-host password=; do
  if grep -Fq "$forbidden" "$tmp/log"; then
    echo 'FAIL: database query or credential appeared in boot log' >&2
    exit 1
  fi
done
grep -Fq "postgres 127.0.0.1:$port" "$tmp/log"
echo 'PASS: query @ neither leaks nor redirects the real database probe'

# Run the watchdog unchanged at its subprocess/configuration boundary. Fake
# only launchd and HTTP: no service restarts or user sessions are touched.
cat > "$tmp/bin/launchctl" <<'LAUNCHCTL'
#!/bin/bash
case "$1" in
  print) exit 0 ;;
  kickstart) echo kickstart >> "$WATCHDOG_ACTIONS" ;;
  *) exit 1 ;;
esac
LAUNCHCTL
cat > "$tmp/bin/curl" <<'CURL'
#!/bin/bash
printf 503
CURL
chmod +x "$tmp/bin/launchctl" "$tmp/bin/curl"
python3 - "$tmp/service.plist" <<'PLIST'
import plistlib
import sys
with open(sys.argv[1], "wb") as target:
    plistlib.dump({"EnvironmentVariables": {}}, target)
PLIST
run_watchdog() {
  env -u AGENT_LB_DATABASE_URL HOME="$tmp/home" PATH="$tmp/bin:$PATH" \
    WATCHDOG_ACTIONS="$tmp/actions" AGENT_LB_PLIST="$tmp/service.plist" \
    AGENT_LB_WATCHDOG_STATE="$tmp/state" AGENT_LB_WATCHDOG_LOG="$tmp/watchdog.log" \
    AGENT_LB_WATCHDOG_THRESHOLD=1 AGENT_LB_RESTART_BIN="$tmp/no-restart" \
    bash "$root/scripts/watchdog.sh"
}
run_watchdog
# Reaching kickstart proves db_ready returned 0 for the absent database key.
grep -q kickstart "$tmp/actions"
echo 'PASS: absent database key permits default SQLite recovery'

rm -f "$tmp/actions" "$tmp/state" "$tmp/watchdog.log"
# A present lookup tool that fails is different from a successfully absent key.
cat > "$tmp/bin/python3" <<'LOOKUP'
#!/bin/bash
exit 1
LOOKUP
chmod +x "$tmp/bin/python3"
run_watchdog
[[ ! -e "$tmp/actions" ]]
grep -q 'dependency readiness unknown, not restarting' "$tmp/watchdog.log"
echo 'PASS: failed configuration lookup blocks watchdog recovery'
