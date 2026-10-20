#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LABEL=com.aneyman.agent-lb-account-health
BIN="$HOME/.agent-lb/bin/account-health-watch"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
mkdir -p "$(dirname "$BIN")" "$(dirname "$PLIST")" "$HOME/.agent-lb/state"
install -m 755 "$SCRIPT_DIR/account-health-watch" "$BIN"
python3 - "$PLIST" "$BIN" "$HOME" <<'PY'
import os
import plistlib
import sys
from pathlib import Path

path, binary, home = sys.argv[1:]
env = {"PATH": os.environ["PATH"]}
for name in ("BASE_URL", "AGENT_LB_STATUS_COOKIE"):
    if name in os.environ:
        env[name] = os.environ[name]
plist = {
    "Label": "com.aneyman.agent-lb-account-health",
    "ProgramArguments": ["/usr/bin/python3", binary],
    "StartInterval": 300,
    "RunAtLoad": True,
    "EnvironmentVariables": env,
    "StandardErrorPath": home + "/.agent-lb/state/account-health.err.log",
}
Path(path).write_bytes(plistlib.dumps(plist))
Path(path).chmod(0o600)
PY
plutil -lint "$PLIST" >/dev/null
if launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null; then
    :
fi
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "installed $LABEL -> $BIN"
