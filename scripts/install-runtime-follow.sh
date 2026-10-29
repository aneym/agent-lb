#!/usr/bin/env bash
set -euo pipefail

LABEL=com.aneyman.agent-lb-follow
OTHER=com.agent-rails.agent-lb-follow
CLI_ONLY=0
PRINT=0
for arg in "$@"; do
  case "$arg" in
    --print) PRINT=1 ;;
    --cli-only) CLI_ONLY=1 ;;
    *) printf 'usage: %s [--cli-only] [--print]\n' "$0" >&2; exit 2 ;;
  esac
done
if [[ "$CLI_ONLY" == 1 ]]; then
  if [[ -e "$HOME/Library/LaunchAgents/com.aneyman.agent-lb.plist" ]]; then
    printf 'install-runtime-follow: --cli-only refused; this host has the agent-lb service plist\n' >&2
    exit 2
  fi
  LABEL=com.agent-rails.agent-lb-follow
  OTHER=com.aneyman.agent-lb-follow
fi
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

gen_plist() {
  python3 - "$HOME" "$LABEL" "$CLI_ONLY" <<'PY'
import plistlib
import shutil
import sys
from pathlib import Path

home = Path(sys.argv[1])
label, cli_only = sys.argv[2], sys.argv[3] == "1"
follower = home / ".agent-lb/runtime/agent-lb/scripts/runtime_follow.py"
python = "/opt/homebrew/bin/python3" if Path("/opt/homebrew/bin/python3").exists() else shutil.which("python3")
bun = home / ".bun/bin/bun"
ext_run = home / ".local/bin/ext-run.js"
args = [python, str(follower), "tick"]
if bun.exists() and ext_run.exists():
    args = [str(bun), str(ext_run), *args]
plist = {
    "Label": label,
    "ProgramArguments": args,
    "RunAtLoad": True,
    "StartInterval": 300,
    "ProcessType": "Interactive",
    "EnvironmentVariables": {
        "PATH": f"{home}/.bun/bin:{home}/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        **({"AGENT_LB_FOLLOW_CLI_ONLY": "1"} if cli_only else {}),
    },
    "StandardOutPath": str(home / ".agent-lb/follow.log"),
    "StandardErrorPath": str(home / ".agent-lb/follow.log"),
}
plistlib.dump(plist, sys.stdout.buffer, sort_keys=True)
PY
}

if [[ "$PRINT" == 1 ]]; then
  gen_plist
  exit 0
fi

mkdir -p "$(dirname "$PLIST")"
TEMP="$(mktemp "$PLIST.XXXXXX")"
trap 'rm -f "$TEMP"' EXIT
gen_plist > "$TEMP"
plutil -lint "$TEMP" >/dev/null
mv -f "$TEMP" "$PLIST"
launchctl bootout "gui/$UID/$OTHER" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/$OTHER.plist"
launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$UID" "$PLIST"
