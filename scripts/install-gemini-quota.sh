#!/bin/bash
set -euo pipefail
RUNTIME="${AGENT_LB_RUNTIME_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
LOGS="$HOME/.agent-lb"
AGY_DIR="${AGENT_LB_GEMINI_AGY_DIR:-$HOME/.gemini/antigravity-cli}"
PUSH="${AGENT_LB_GEMINI_QUOTA_PUSH-ax42:.agent-lb/seats}"
PLAN="${AGENT_LB_GEMINI_PLAN:-Google AI Ultra 20x}"
VENDOR="${AGENT_LB_GEMINI_VENDOR:-off}"
METER="${AGENT_LB_GEMINI_METER:-$HOME/.agent-lb/seats/gemini-meter.json}"
USAGE="${AGENT_LB_GEMINI_USAGE:-$HOME/.agent-lb/seats/gemini.json}"
SEND_PATH="$HOME/.local/bin:$HOME/.agent-rails/factory-runtime/bin"
mkdir -p "$LOGS" "$(dirname "$METER")" "$(dirname "$USAGE")"

UNITS="$HOME/.config/systemd/user"
mkdir -p "$UNITS"
cat > "$UNITS/agent-lb-gemini-quota.service" <<UNIT
[Unit]
Description=agent-lb Gemini quota seat: meter agy's store and write the seat reading

[Service]
Type=oneshot
WorkingDirectory=$RUNTIME
Environment=PATH=$SEND_PATH:/usr/local/bin:/usr/bin:/bin
Environment="AGENT_LB_GEMINI_AGY_DIR=$AGY_DIR"
Environment="AGENT_LB_GEMINI_QUOTA_PUSH=$PUSH"
Environment="AGENT_LB_GEMINI_PLAN=$PLAN"
Environment="AGENT_LB_GEMINI_VENDOR=$VENDOR"
Environment="AGENT_LB_GEMINI_METER=$METER"
Environment="AGENT_LB_GEMINI_USAGE=$USAGE"
ExecStart=$RUNTIME/.venv/bin/python -m app.modules.pools.gemini_quota_collect
StandardOutput=append:$LOGS/gemini-quota.log
StandardError=append:$LOGS/gemini-quota.log
Nice=15
TimeoutStartSec=60
UNIT
cat > "$UNITS/agent-lb-gemini-quota.timer" <<UNIT
[Unit]
Description=Run the agent-lb Gemini quota collector every 5 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
RandomizedDelaySec=30

[Install]
WantedBy=timers.target
UNIT
systemctl --user daemon-reload
systemctl --user enable --now agent-lb-gemini-quota.timer
echo "installed agent-lb-gemini-quota.timer every 5 min: vendor $VENDOR, push ${PUSH:-none}, runtime $RUNTIME"
