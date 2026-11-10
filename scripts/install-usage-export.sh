#!/bin/bash
set -euo pipefail
RUNTIME="${AGENT_LB_RUNTIME_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
TARGET_HOST="${AGENT_LB_USAGE_EXPORT_HOST:-ax102}"
TARGET_DIR="${AGENT_LB_USAGE_EXPORT_DIR:-/home/jobs/.agent-rails/factory/agent-lb-export}"
LOGS="$HOME/.agent-lb"
mkdir -p "$LOGS"

if [[ "$(hostname -s)" == "$TARGET_HOST" ]]; then
  ARGS=(--out "$TARGET_DIR")
else
  ARGS=(--out "$LOGS/usage-export" --push "$TARGET_HOST:$TARGET_DIR")
fi
ARGS+=(--alert-state "$LOGS/usage-export-alert.json" --asked-by "$LOGS/asked-by")
for SOURCE in ${AGENT_LB_ASKED_BY_SOURCES:-ax102:.agent-rails/asked-by studio:.agent-rails/asked-by}; do
  ARGS+=(--asked-by-pull "$SOURCE")
done
SEND_PATH="$HOME/.local/bin:$HOME/.agent-rails/factory-runtime/bin"

if [[ "$(uname)" == Darwin ]]; then
  LABEL=com.agentlb.usage-export
  PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
  python3 - "$PLIST" "$RUNTIME" "$LOGS" "$SEND_PATH" "${ARGS[@]}" <<'PY'
import plistlib
import sys
from pathlib import Path

path, runtime, logs, send_path, *args = sys.argv[1:]
plist = {
    "Label": "com.agentlb.usage-export",
    "ProgramArguments": [runtime + "/.venv/bin/python", "-m", "app.usage_export", *args],
    "WorkingDirectory": runtime,
    "EnvironmentVariables": {"PATH": send_path + ":/opt/homebrew/bin:/usr/bin:/bin"},
    "StartInterval": 900,
    "RunAtLoad": True,
    "Nice": 15,
    "StandardOutPath": logs + "/usage-export.log",
    "StandardErrorPath": logs + "/usage-export.log",
}
Path(path).write_bytes(plistlib.dumps(plist))
PY
  plutil -lint "$PLIST" >/dev/null
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$PLIST"
  echo "installed $LABEL every 15 min: ${ARGS[*]}"
  exit 0
fi

UNITS="$HOME/.config/systemd/user"
mkdir -p "$UNITS"
cat > "$UNITS/agent-lb-usage-export.service" <<UNIT
[Unit]
Description=agent-lb usage export: hourly usage and Anthropic headroom JSON for $TARGET_HOST:$TARGET_DIR

[Service]
Type=oneshot
WorkingDirectory=$RUNTIME
Environment=PATH=$SEND_PATH:/usr/local/bin:/usr/bin:/bin
ExecStart=$RUNTIME/.venv/bin/python -m app.usage_export ${ARGS[*]}
StandardOutput=append:$LOGS/usage-export.log
StandardError=append:$LOGS/usage-export.log
Nice=15
TimeoutStartSec=300
UNIT
cat > "$UNITS/agent-lb-usage-export.timer" <<UNIT
[Unit]
Description=Run the agent-lb usage export every 15 minutes

[Timer]
OnBootSec=5min
OnUnitActiveSec=15min

[Install]
WantedBy=timers.target
UNIT
systemctl --user daemon-reload
systemctl --user enable --now agent-lb-usage-export.timer
echo "installed agent-lb-usage-export.timer every 15 min: ${ARGS[*]}"
