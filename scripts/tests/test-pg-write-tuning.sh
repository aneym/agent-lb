#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/pg-write-tuning-test.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }
export CALL_LOG="$TMP/calls" STATE="$TMP/state"
mkdir -p "$TMP/bin"
: > "$CALL_LOG"
echo pending > "$STATE"
cat > "$TMP/bin/psql" <<'PY'
#!/usr/bin/env python3
import os, sys
sql = sys.argv[-1]
state = open(os.environ["STATE"]).read().strip()
live = {"work_mem": "32MB", "wal_compression": "lz4", "checkpoint_timeout": "15min",
        "max_wal_size": "4GB", "shared_buffers": "2GB" if state == "restarted" else "128MB",
        "data_directory": "/fixture/pgdata"}
if "pending_restart" in sql:
    print(0 if state == "restarted" else 1)
elif sql.startswith("SHOW "):
    print(live[sql.split()[1]])
PY
cat > "$TMP/bin/pg_ctl" <<'SH2'
#!/usr/bin/env bash
printf 'pg_ctl %s\n' "$*" >> "$CALL_LOG"
echo restarted > "$STATE"
SH2
cat > "$TMP/bin/launchctl" <<'SH2'
#!/usr/bin/env bash
printf 'launchctl %s\n' "$*" >> "$CALL_LOG"
SH2
cat > "$TMP/bin/pg_isready" <<'SH2'
#!/usr/bin/env bash
exit 0
SH2
chmod 755 "$TMP/bin/"*
export PATH="$TMP/bin:$PATH" PSQL="$TMP/bin/psql" PG_CTL="$TMP/bin/pg_ctl" PG_ISREADY="$TMP/bin/pg_isready"
OUT="$("$ROOT/scripts/pg-write-tuning.sh" --restart)" || fail "restart run failed: $OUT"
grep -qx 'pg_ctl -D /fixture/pgdata stop -m fast -w -t 60' "$CALL_LOG" || fail "no fast shutdown: $(cat "$CALL_LOG")"
grep -q 'kickstart -k' "$CALL_LOG" && fail 'kickstart -k sends SIGTERM, a smart shutdown that waits on pooled clients'
grep -qx 'ok shared_buffers=2GB' <<< "$OUT" || fail "shared_buffers not live: $OUT"
: > "$CALL_LOG"
"$ROOT/scripts/pg-write-tuning.sh" --restart >/dev/null || fail 'second run failed'
[[ ! -s "$CALL_LOG" ]] || fail "restarted with nothing pending: $(cat "$CALL_LOG")"
printf 'PASS: --restart stops postgres with a fast shutdown and skips the restart when nothing is pending\n'
