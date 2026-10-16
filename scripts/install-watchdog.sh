#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LABEL=com.aneyman.agent-lb-watchdog
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
BIN_DIR="$HOME/.agent-lb/bin"

case "${1:-}" in
  --print|"") ;;
  *) printf 'usage: %s [--print]\n' "$0" >&2; exit 2 ;;
esac
if (($# > 1)); then
  printf 'usage: %s [--print]\n' "$0" >&2
  exit 2
fi

gen_plist() {
  python3 - "$PLIST" "$HOME" <<'PY'
import plistlib
import sys
from pathlib import Path

path = Path(sys.argv[1])
home = Path(sys.argv[2])
existing = plistlib.loads(path.read_bytes()) if path.exists() else {}
plist = {
    "Label": "com.aneyman.agent-lb-watchdog",
    "ProgramArguments": ["/bin/bash", str(home / ".agent-lb/bin/watchdog.sh")],
    "RunAtLoad": True,
    "StartInterval": 30,
    "ProcessType": "Interactive",
    "StandardOutPath": str(home / ".agent-lb/watchdog.out.log"),
    "StandardErrorPath": str(home / ".agent-lb/watchdog.err.log"),
}
if "EnvironmentVariables" in existing:
    plist["EnvironmentVariables"] = existing["EnvironmentVariables"]
plistlib.dump(plist, sys.stdout.buffer, sort_keys=True)
PY
}

if [[ "${1:-}" == --print ]]; then
  gen_plist
  exit 0
fi

mkdir -p "$BIN_DIR" "$(dirname "$PLIST")"
TEMP=""
trap 'if [[ -n "$TEMP" ]]; then rm -f "$TEMP"; fi' EXIT
for FILE in watchdog.sh db-endpoint.sh pg-qos-guard.sh; do
  TEMP="$(mktemp "$BIN_DIR/$FILE.XXXXXX")"
  cp "$SCRIPT_DIR/$FILE" "$TEMP"
  chmod 755 "$TEMP"
  mv -f "$TEMP" "$BIN_DIR/$FILE"
done
TEMP="$(mktemp "$PLIST.XXXXXX")"
gen_plist > "$TEMP"
plutil -lint "$TEMP" >/dev/null
mv -f "$TEMP" "$PLIST"
TEMP=""
if launchctl bootout "gui/$UID/$LABEL" 2>/dev/null; then
  :
fi
launchctl bootstrap "gui/$UID" "$PLIST"
