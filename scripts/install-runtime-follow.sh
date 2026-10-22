#!/usr/bin/env bash
set -euo pipefail

LABEL=com.aneyman.agent-lb-follow
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

case "${1:-}" in
  --print|"") ;;
  *) printf 'usage: %s [--print]\n' "$0" >&2; exit 2 ;;
esac

gen_plist() {
  python3 - "$HOME" <<'PY'
import plistlib
import shutil
import sys
from pathlib import Path

home = Path(sys.argv[1])
follower = home / ".agent-lb/runtime/agent-lb/scripts/runtime_follow.py"
python = "/opt/homebrew/bin/python3" if Path("/opt/homebrew/bin/python3").exists() else shutil.which("python3")
bun = home / ".bun/bin/bun"
ext_run = home / ".local/bin/ext-run.js"
args = [python, str(follower), "tick"]
if bun.exists() and ext_run.exists():
    args = [str(bun), str(ext_run), *args]
plist = {
    "Label": "com.aneyman.agent-lb-follow",
    "ProgramArguments": args,
    "RunAtLoad": True,
    "StartInterval": 300,
    "EnvironmentVariables": {
        "PATH": f"{home}/.bun/bin:{home}/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
    },
    "StandardOutPath": str(home / ".agent-lb/follow.log"),
    "StandardErrorPath": str(home / ".agent-lb/follow.log"),
}
plistlib.dump(plist, sys.stdout.buffer, sort_keys=True)
PY
}

if [[ "${1:-}" == --print ]]; then
  gen_plist
  exit 0
fi

mkdir -p "$(dirname "$PLIST")"
TEMP="$(mktemp "$PLIST.XXXXXX")"
trap 'rm -f "$TEMP"' EXIT
gen_plist > "$TEMP"
plutil -lint "$TEMP" >/dev/null
mv -f "$TEMP" "$PLIST"
launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$UID" "$PLIST"
