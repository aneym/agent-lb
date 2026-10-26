#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="$HOME/.agent-lb/bin/t3-thread-collector"
mkdir -p "$(dirname "$BIN")" "$HOME/.agent-lb/state"
install -m 755 "$SCRIPT_DIR/t3-thread-collector" "$BIN"

case "$(uname -s)" in
Darwin)
    LABEL=com.aneyman.agent-lb-t3-collector
    PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
    mkdir -p "$(dirname "$PLIST")"
    python3 - "$PLIST" "$BIN" "$HOME" <<'PY'
import plistlib
import sys
from pathlib import Path

path, binary, home = sys.argv[1:]
plist = {
    "Label": "com.aneyman.agent-lb-t3-collector",
    "ProgramArguments": ["/usr/bin/python3", binary, "--host", "studio"],
    "StartInterval": 120,
    "RunAtLoad": True,
    "StandardErrorPath": home + "/.agent-lb/state/t3-collector.err.log",
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
    ;;
Linux)
    UNIT_DIR="$HOME/.config/systemd/user"
    mkdir -p "$UNIT_DIR"
    cat >"$UNIT_DIR/agent-lb-t3-collector.service" <<'UNIT'
[Unit]
Description=Send T3 thread names and native session ids to agent-lb

[Service]
Type=oneshot
Environment=AGENT_LB_BASE_URL=http://127.0.0.1:2457
ExecStart=/usr/bin/python3 %h/.agent-lb/bin/t3-thread-collector --host ax42
Nice=10
TimeoutStartSec=60
UNIT
    cat >"$UNIT_DIR/agent-lb-t3-collector.timer" <<'UNIT'
[Unit]
Description=Run the agent-lb T3 thread collector every two minutes

[Timer]
OnBootSec=60
OnUnitActiveSec=120

[Install]
WantedBy=timers.target
UNIT
    systemctl --user daemon-reload
    systemctl --user enable --now agent-lb-t3-collector.timer
    echo "installed agent-lb-t3-collector.timer -> $BIN"
    ;;
*)
    echo "unsupported platform: $(uname -s)" >&2
    exit 1
    ;;
esac
