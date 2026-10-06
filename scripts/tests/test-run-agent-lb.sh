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
url='postgresql+asyncpg://fixture-user:fixture-prefix/?#fixture-tail@127.0.0.1:1/db?password=fixture-query'
HOME="$tmp/home" AGENT_LB_DATABASE_URL="$url" AGENT_LB_DB_WAIT_SECONDS=1 \
  bash "$root/scripts/run-agent-lb.sh" >"$tmp/log" 2>&1
for forbidden in fixture-user fixture-prefix fixture-tail fixture-query password=; do
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
