#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/pg-qos-test.XXXXXX")"
PARENT=""
CHILDREN=""
cleanup() {
  local pid
  for pid in $CHILDREN $PARENT; do
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid"
    fi
  done
  if [[ -n "$PARENT" ]]; then
    if wait "$PARENT" 2>/dev/null; then :; fi
  fi
  rm -rf "$TMP"
}
trap cleanup EXIT
fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }
export HOME="$TMP/home"
export CALL_LOG="$TMP/calls"
mkdir -p "$TMP/bin" "$HOME/Library/LaunchAgents"
: > "$CALL_LOG"
for tool in launchctl brew; do
  cat > "$TMP/bin/$tool" <<'SH'
#!/usr/bin/env bash
printf '%s %s\n' "${0##*/}" "$*" >> "$CALL_LOG"
exit 1
SH
  chmod 755 "$TMP/bin/$tool"
done
export PATH="$TMP/bin:$PATH"
PLIST="$HOME/Library/LaunchAgents/sh.brew.postgresql@17.plist"
LOG="$TMP/guard.log"
python3 - "$PLIST" <<'PY'
import plistlib
import sys
from pathlib import Path

Path(sys.argv[1]).write_bytes(plistlib.dumps({
    "Label": "sh.brew.postgresql@17",
    "ProgramArguments": ["/opt/homebrew/bin/postgres", "-D", "/fixture"],
    "RunAtLoad": True,
}))
PY
cat > "$TMP/postgres.c" <<'C'
#include <unistd.h>
#include <sys/wait.h>
int main(void) {
    for (int i = 0; i < 2; i++) {
        pid_t pid = fork();
        if (pid < 0) return 1;
        if (pid == 0) {
            sleep(120);
            _exit(0);
        }
    }
    while (wait(0) > 0) {}
    return 0;
}
C
clang -o "$TMP/bin/postgres" "$TMP/postgres.c"
"$TMP/bin/postgres" &
PARENT=$!
for ((attempt=0; attempt<200; attempt++)); do
  CHILDREN="$(ps -ax -o pid=,ppid= | awk -v parent="$PARENT" '$2 == parent {print $1}')"
  [[ "$(wc -w <<< "$CHILDREN" | tr -d ' ')" == 2 ]] && break
done
[[ "$(wc -w <<< "$CHILDREN" | tr -d ' ')" == 2 ]] || fail 'fake postmaster did not start two children'
for pid in $PARENT $CHILDREN; do
  taskpolicy -b -p "$pid"
  pri="$(ps -o pri= -p "$pid")"
  (( pri <= 4 )) || fail "fixture pid $pid not clamped: $pri"
done
GUARD="$ROOT/scripts/pg-qos-guard.sh"
if "$GUARD" --check --plist "$PLIST" --pid "$PARENT" --log "$LOG"; then
  fail 'check missed drift'
else
  [[ "$?" == 1 ]] || fail 'check returned unexpected status'
fi
[[ ! -e "$LOG" ]] || fail 'check wrote log'
if plutil -extract ProcessType raw "$PLIST" >/dev/null 2>&1; then
  fail 'check changed plist'
fi
"$GUARD" --plist "$PLIST" --pid "$PARENT" --log "$LOG"
[[ "$(plutil -extract ProcessType raw "$PLIST")" == Interactive ]] || fail 'plist not Interactive'
plutil -lint "$PLIST"
for pid in $PARENT $CHILDREN; do
  pri="$(ps -o pri= -p "$pid")"
  (( pri > 4 )) || fail "pid $pid still clamped: $pri"
done
"$GUARD" --check --plist "$PLIST" --pid "$PARENT" --log "$LOG"
[[ "$(wc -l < "$LOG" | tr -d ' ')" == 2 ]] || fail 'expected one plist event and one process event'
grep -Eq 'cleared background QoS for 3 processes; postmaster=yes$' "$LOG" || fail 'missing repair event'
SUM="$(shasum -a 256 "$PLIST")"
LOG_SUM="$(shasum -a 256 "$LOG")"
"$GUARD" --plist "$PLIST" --pid "$PARENT" --log "$LOG"
[[ "$(shasum -a 256 "$PLIST")" == "$SUM" ]] || fail 'second apply changed plist'
[[ "$(shasum -a 256 "$LOG")" == "$LOG_SUM" ]] || fail 'second apply wrote log'
python3 - "$HOME/Library/LaunchAgents" <<'PY'
import plistlib
import sys
from pathlib import Path

root = Path(sys.argv[1])
(root / "com.aneyman.agent-lb.plist").write_bytes(plistlib.dumps({"ProcessType": "Background"}))
(root / "com.aneyman.agent-lb-watchdog.plist").write_bytes(plistlib.dumps({
    "ProcessType": "Background", "Nice": 15, "LowPriorityIO": True,
    "EnvironmentVariables": {"OF_OVERLAY": "/fixture/overlay"},
}))
PY
bash "$ROOT/scripts/install-service.sh" --print > "$TMP/service.plist"
bash "$ROOT/scripts/install-watchdog.sh" --print > "$TMP/watchdog.plist"
python3 - "$TMP/service.plist" "$TMP/watchdog.plist" <<'PY'
import os
import plistlib
import sys
from pathlib import Path

service, watchdog = [plistlib.loads(Path(p).read_bytes()) for p in sys.argv[1:]]
assert service["ProcessType"] == "Interactive"
assert watchdog["ProcessType"] == "Interactive"
assert watchdog["StartInterval"] == 30
assert watchdog["RunAtLoad"] is True
assert watchdog["ProgramArguments"] == ["/bin/bash", str(Path.home() / ".agent-lb/bin/watchdog.sh")]
assert watchdog["StandardOutPath"] == str(Path.home() / ".agent-lb/watchdog.out.log")
assert watchdog["StandardErrorPath"] == str(Path.home() / ".agent-lb/watchdog.err.log")
assert watchdog["EnvironmentVariables"] == {"OF_OVERLAY": "/fixture/overlay"}
assert "Nice" not in watchdog and "LowPriorityIO" not in watchdog
assert not (Path(os.environ["HOME"]) / ".agent-lb/bin").exists()
PY
[[ ! -s "$CALL_LOG" ]] || fail 'launchctl or brew was called'
printf 'PASS: real parent and children QoS repaired; plist Interactive; check read-only; apply idempotent\n'
printf 'PASS: service and watchdog plists Interactive; environment preserved; no launchctl or brew calls\n'
