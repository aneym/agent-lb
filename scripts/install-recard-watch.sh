#!/usr/bin/env bash
set -euo pipefail

LABEL=com.aneyman.agent-lb-recard
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
AGENT_LB="$(command -v agent-lb)"
mkdir -p "$(dirname "$PLIST")" "$HOME/.agent-lb/state"
python3 - "$PLIST" "$AGENT_LB" "$HOME" <<'PY'
import os
import plistlib
import sys
from pathlib import Path

path, binary, home = sys.argv[1:]
plist = {
    "Label": "com.aneyman.agent-lb-recard",
    "ProgramArguments": [binary, "recard", "run"],
    "StartInterval": 900,
    "RunAtLoad": True,
    "EnvironmentVariables": {"PATH": os.environ["PATH"]},
    "StandardOutPath": home + "/.agent-lb/state/recard.out.log",
    "StandardErrorPath": home + "/.agent-lb/state/recard.err.log",
}
Path(path).write_bytes(plistlib.dumps(plist))
Path(path).chmod(0o600)
PY
plutil -lint "$PLIST" >/dev/null
if launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null; then
    :
fi
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "installed $LABEL -> $AGENT_LB recard run"
