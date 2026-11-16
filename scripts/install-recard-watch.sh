#!/usr/bin/env bash
set -euo pipefail

LABEL=com.aneyman.agent-lb-recard
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
AGENT_LB="$(command -v agent-lb)"
STATE="$HOME/.agent-lb/state"
LEDGER_SSH="${AGENT_LB_LEDGER_SSH:-}"
LEDGER_PATH="${AGENT_LB_LEDGER_PATH:-.agent-lb-primary/state/subscriptions.json}"
LEAD_DAYS="${AGENT_LB_RECARD_LEAD_DAYS:-3}"
[[ "$LEAD_DAYS" =~ ^[0-9]+$ ]] || { echo "AGENT_LB_RECARD_LEAD_DAYS must be a whole number of days" >&2; exit 2; }
mkdir -p "$(dirname "$PLIST")" "$STATE"
if [[ -n "$LEDGER_SSH" ]]; then
    ssh -o BatchMode=yes -o ConnectTimeout=10 "$LEDGER_SSH" test -f "$LEDGER_PATH" \
        || { echo "no subscription ledger at $LEDGER_SSH:$LEDGER_PATH" >&2; exit 2; }
    python3 - "$STATE/ledger.json" "$LEDGER_SSH" "$LEDGER_PATH" <<'PY'
import json
import sys
from pathlib import Path

path, host, remote = sys.argv[1:]
Path(path).write_text(json.dumps({"ssh": host, "path": remote}, indent=2) + "\n")
Path(path).chmod(0o600)
PY
    if [[ -f "$STATE/subscriptions.json" ]]; then
        mv "$STATE/subscriptions.json" "$STATE/subscriptions.json.before-$LEDGER_SSH-$(date +%Y%m%d)"
    fi
    echo "subscription ledger -> $LEDGER_SSH:$LEDGER_PATH"
fi
python3 - "$PLIST" "$AGENT_LB" "$HOME" "$LEAD_DAYS" <<'PY'
import os
import plistlib
import sys
from pathlib import Path

path, binary, home, lead_days = sys.argv[1:]
plist = {
    "Label": "com.aneyman.agent-lb-recard",
    "ProgramArguments": [binary, "recard", "run"],
    "StartInterval": 900,
    "RunAtLoad": True,
    "EnvironmentVariables": {"PATH": os.environ["PATH"], "AGENT_LB_RECARD_LEAD_DAYS": lead_days},
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
echo "installed $LABEL -> $AGENT_LB recard run, starting $LEAD_DAYS days before renewal"
