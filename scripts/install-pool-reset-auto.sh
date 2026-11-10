#!/bin/bash
set -euo pipefail
RUNTIME="${AGENT_LB_RUNTIME_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
PYTHON="${AGENT_LB_PYTHON:-$RUNTIME/.venv/bin/python}"
SEND_HOST="${AGENT_LB_POOL_RESET_SEND_HOST:-ax102}"
STATE="${AGENT_LB_POOL_RESET_STATE:-$HOME/.agent-rails/factory/pool-reset-auto}"
MIRROR="${AGENT_LB_POOL_RESET_MIRROR:-$SEND_HOST:/home/jobs/.agent-rails/factory/pool-reset-auto}"
LOGS="$HOME/.agent-lb"
UNIT=agent-lb-pool-reset-auto

if [[ "$(uname)" == Darwin ]]; then
  echo "install-pool-reset-auto: runs on the Linux primary that owns the pool accounts" >&2
  exit 2
fi
if [[ "$(hostname -s)" == "$SEND_HOST" ]]; then
  SEND_ARGS=()
else
  SEND_ARGS=(--send-host "$SEND_HOST" --push "$MIRROR")
fi
mkdir -p "$LOGS" "$STATE"
(cd "$RUNTIME" && "$PYTHON" -c "import app.pool_reset_auto") >/dev/null

UNITS="$HOME/.config/systemd/user"
mkdir -p "$UNITS"
cat > "$UNITS/$UNIT.service" <<SERVICE
[Unit]
Description=agent-lb pool reset: redeem a banked Claude reset when the pool is about to run dry

[Service]
Type=oneshot
WorkingDirectory=$RUNTIME
Environment=PATH=$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=$PYTHON -m app.pool_reset_auto --live --state $STATE ${SEND_ARGS[*]}
StandardOutput=append:$LOGS/pool-reset-auto.log
StandardError=append:$LOGS/pool-reset-auto.log
Nice=10
TimeoutStartSec=420
SERVICE
cat > "$UNITS/$UNIT.timer" <<TIMER
[Unit]
Description=Run the agent-lb pool reset check every 5 minutes

[Timer]
OnCalendar=*:0/5
AccuracySec=10s

[Install]
WantedBy=timers.target
TIMER
systemctl --user daemon-reload
systemctl --user enable --now "$UNIT.timer"
echo "installed $UNIT.timer every 5 min from $RUNTIME: state $STATE ${SEND_ARGS[*]}"
