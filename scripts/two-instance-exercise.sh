#!/usr/bin/env bash
# Two-instance federation checkout/checkin round-trip exercise against two REAL
# running agent-lb instances on localhost (aiohttp peer client + HTTP layer,
# previously mock-only). Synthetic tokens only; servers bound to 127.0.0.1.
set -u

REPO="$(cd "$(dirname "$0")/.." && pwd)"
RUNROOT="$(mktemp -d "${TMPDIR:-/tmp}/agent-lb-fed-exercise.XXXXXX")"
PY="$REPO/.venv/bin/python"
HELPER="$REPO/scripts/two_instance_fedhelper.py"
RUN="$RUNROOT/run"
LOGS="$RUNROOT/logs"
ACCT="acct-fed-test"
PORT_A=3501
PORT_B=3502
TOKEN="$("$PY" -c 'import secrets;print(secrets.token_urlsafe(24))')"

A_DIR="$RUN/alpha"
B_DIR="$RUN/beta"
DB_A="$A_DIR/store.db"
DB_B="$B_DIR/store.db"
KEY_A="$A_DIR/encryption.key"
KEY_B="$B_DIR/encryption.key"

PASSED=0
FAILED=0
PID_A=""
PID_B=""
START=$(date +%s)

pass() { echo "PASS: $1"; PASSED=$((PASSED + 1)); }
fail() { echo "FAIL: $1"; FAILED=$((FAILED + 1)); }
assert_eq() { # label expected actual
  if [ "$2" = "$3" ]; then pass "$1 ($2)"; else fail "$1 expected=[$2] actual=[$3]"; fi
}

cleanup() {
  # Kill by listening port: PID_A/PID_B are the wrapper subshells, and killing
  # only those orphans the uvicorn children (observed 2026-07-03 — stale
  # servers then poison the next run's clean start).
  lsof -tiTCP:$PORT_A -tiTCP:$PORT_B -sTCP:LISTEN 2>/dev/null | xargs kill 2>/dev/null
  [ -n "$PID_A" ] && kill "$PID_A" 2>/dev/null
  [ -n "$PID_B" ] && kill "$PID_B" 2>/dev/null
  sleep 1
  lsof -tiTCP:$PORT_A -tiTCP:$PORT_B -sTCP:LISTEN 2>/dev/null | xargs kill -9 2>/dev/null
  rm -rf "$RUN"
}
trap cleanup EXIT

hlp() { "$PY" "$HELPER" "$@"; }
db_owner() { hlp owner "$1" "$ACCT"; }

wait_http() { # port
  for _ in $(seq 1 60); do
    code=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$1/api/federation/mirror" 2>/dev/null)
    [ "$code" != "000" ] && return 0
    sleep 0.5
  done
  return 1
}

boot() { # dir port instance_id extra_env_file
  local dir="$1" port="$2" iid="$3" logf="$4"
  shift 4
  ( cd "$REPO" && env \
      AGENT_LB_DATA_DIR="$dir" \
      AGENT_LB_DATABASE_URL="sqlite+aiosqlite:///$dir/store.db" \
      AGENT_LB_ENCRYPTION_KEY_FILE="$dir/encryption.key" \
      AGENT_LB_LOCAL_INSTANCE_ID="$iid" \
      AGENT_LB_FEDERATION_TOKEN="$TOKEN" \
      AGENT_LB_USAGE_REFRESH_ENABLED=false \
      AGENT_LB_ACCOUNT_PULSE_ENABLED=false \
      AGENT_LB_QUOTA_PLANNER_SCHEDULER_ENABLED=false \
      AGENT_LB_MODEL_REGISTRY_ENABLED=false \
      AGENT_LB_STICKY_SESSION_CLEANUP_ENABLED=false \
      AGENT_LB_PUBLIC_USAGE_ENABLED=false \
      "$@" \
      "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port "$port" --log-level warning \
      >"$logf" 2>&1 ) &
}

# ---- setup ----
rm -rf "$RUN"; mkdir -p "$A_DIR" "$B_DIR" "$LOGS"
"$PY" -c "from cryptography.fernet import Fernet;open('$KEY_A','wb').write(Fernet.generate_key())"
"$PY" -c "from cryptography.fernet import Fernet;open('$KEY_B','wb').write(Fernet.generate_key())"
chmod 600 "$KEY_A" "$KEY_B"

# Synthetic tokens (access = unsigned JWT with far-future exp so mirror rows read fresh)
A_ACCESS0=$(hlp mkjwt 86400); A_REFRESH0="refresh-seed-$("$PY" -c 'import secrets;print(secrets.token_hex(6))')"
A_ACCESS1=$(hlp mkjwt 86400); A_REFRESH1="refresh-rot1-$("$PY" -c 'import secrets;print(secrets.token_hex(6))')"
A_ACCESS2=$(hlp mkjwt 86400); A_REFRESH2="refresh-rot2-$("$PY" -c 'import secrets;print(secrets.token_hex(6))')"

# Preflight: refuse to run against leftovers from a previous exercise.
if lsof -tiTCP:$PORT_A -tiTCP:$PORT_B -sTCP:LISTEN >/dev/null 2>&1; then
  echo "FATAL: ports $PORT_A/$PORT_B already in use (stale exercise servers?) — kill them first." >&2
  exit 1
fi

echo "Booting ALPHA (owner, :$PORT_A) and BETA (taker, :$PORT_B, mirror 2s)..."
boot "$A_DIR" "$PORT_A" alpha "$LOGS/alpha.log"
PID_A=$!
boot "$B_DIR" "$PORT_B" beta "$LOGS/beta.log" \
  AGENT_LB_FEDERATION_PEER_URL="http://127.0.0.1:$PORT_A" \
  AGENT_LB_FEDERATION_MIRROR_INTERVAL_SECONDS=2
PID_B=$!

if ! wait_http "$PORT_A"; then fail "alpha did not become reachable"; echo "--- alpha.log ---"; tail -30 "$LOGS/alpha.log"; exit 1; fi
if ! wait_http "$PORT_B"; then fail "beta did not become reachable"; echo "--- beta.log ---"; tail -30 "$LOGS/beta.log"; exit 1; fi
echo "Both instances reachable."

# Seed ALPHA's DB with one synthetic, locally-owned account.
hlp seed "$DB_A" "$KEY_A" "$ACCT" "$A_ACCESS0" "$A_REFRESH0" \
  && echo "Seeded ALPHA account $ACCT (owner_instance=NULL)" \
  || { fail "seed failed"; tail -20 "$LOGS/alpha.log"; exit 1; }

echo
echo "==== (a) beta mirror pull materializes the account ===="
mirror_ok=""
for _ in $(seq 1 12); do
  if [ "$(hlp exists "$DB_B" "$ACCT")" = "1" ] && [ "$(db_owner "$DB_B")" = "alpha" ]; then mirror_ok=1; break; fi
  sleep 1
done
if [ -n "$mirror_ok" ]; then pass "a: beta mirrored account owner_instance=alpha within ~2 intervals"; \
  else fail "a: mirror did not materialize (exists=$(hlp exists "$DB_B" "$ACCT") owner=$(db_owner "$DB_B"))"; tail -20 "$LOGS/beta.log"; fi

do_checkout() { # label
  local resp confirmed
  resp=$(curl -s -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
    -d "{\"account_id\":\"$ACCT\"}" "http://127.0.0.1:$PORT_B/api/federation/checkout/execute")
  confirmed=$(printf '%s' "$resp" | "$PY" -c 'import sys,json;print(json.load(sys.stdin).get("confirmed"))' 2>/dev/null)
  assert_eq "$1: checkout confirmed" "True" "$confirmed"
  assert_eq "$1: DB_B owner=beta after checkout" "beta" "$(db_owner "$DB_B")"
  assert_eq "$1: DB_A owner=beta after checkout" "beta" "$(db_owner "$DB_A")"
}

do_checkin() { # label exp_access exp_refresh
  local resp settled dec da dr
  resp=$(curl -s -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
    -d "{\"account_id\":\"$ACCT\"}" "http://127.0.0.1:$PORT_B/api/federation/checkin/execute")
  settled=$(printf '%s' "$resp" | "$PY" -c 'import sys,json;print(json.load(sys.stdin).get("settled"))' 2>/dev/null)
  assert_eq "$1: checkin settled" "True" "$settled"
  assert_eq "$1: DB_A owner=NULL after checkin" "NULL" "$(db_owner "$DB_A")"
  assert_eq "$1: DB_B owner=alpha after checkin" "alpha" "$(db_owner "$DB_B")"
  dec=$(hlp decrypt "$DB_A" "$KEY_A" "$ACCT")
  da=$(printf '%s' "$dec" | cut -f1); dr=$(printf '%s' "$dec" | cut -f2)
  assert_eq "$1: DB_A access decrypts to rotated value" "$2" "$da"
  assert_eq "$1: DB_A refresh decrypts to rotated value" "$3" "$dr"
}

echo
echo "==== (b) first checkout: beta takes ownership ===="
do_checkout "b"

echo
echo "==== (c) taker rotates tokens while checked out (offline refresh) ===="
hlp rotate "$DB_B" "$KEY_B" "$ACCT" "$A_ACCESS1" "$A_REFRESH1" \
  && pass "c: rotated DB_B tokens to ROT1" || fail "c: rotate ROT1 failed"

echo
echo "==== (d) first checkin: rotated tokens settle back to alpha ===="
do_checkin "d" "$A_ACCESS1" "$A_REFRESH1"

echo
echo "==== (e) SECOND round trip (regression for settled-nonce reuse) ===="
do_checkout "e-checkout"
hlp rotate "$DB_B" "$KEY_B" "$ACCT" "$A_ACCESS2" "$A_REFRESH2" \
  && pass "e: rotated DB_B tokens to ROT2" || fail "e: rotate ROT2 failed"
do_checkin "e-checkin" "$A_ACCESS2" "$A_REFRESH2"

echo
echo "==== (f) unauthenticated + wrong-bearer mirror calls are rejected ===="
code_noauth=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT_A/api/federation/mirror")
assert_eq "f: no-auth mirror -> 403" "403" "$code_noauth"
code_wrong=$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer wrong-$TOKEN-x" \
  "http://127.0.0.1:$PORT_A/api/federation/mirror")
assert_eq "f: wrong-bearer mirror -> 403" "403" "$code_wrong"

RUNTIME=$(( $(date +%s) - START ))
echo
echo "SUMMARY: passed=$PASSED failed=$FAILED runtime=${RUNTIME}s head=$(cd "$REPO" && git rev-parse --short HEAD)"
[ "$FAILED" -eq 0 ] && exit 0 || exit 1
