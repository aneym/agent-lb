#!/bin/bash
set -euo pipefail
DAY="${1:?usage: install-pool-reset-surge.sh YYYY-MM-DD approval-record-path}"
APPROVAL="${2:?usage: install-pool-reset-surge.sh YYYY-MM-DD approval-record-path}"
RUNTIME="${AGENT_LB_RUNTIME_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
PYTHON="${AGENT_LB_PYTHON:-$RUNTIME/.venv/bin/python}"
SEND_HOST="${AGENT_LB_POOL_RESET_SEND_HOST:-ax102}"
STATE="${AGENT_LB_POOL_RESET_STATE:-$HOME/.agent-rails/factory/pool-reset-auto}"
LOGS="$HOME/.agent-lb"
UNIT=agent-lb-pool-reset-surge

if [[ "$(uname)" == Darwin ]]; then
  echo "install-pool-reset-surge: runs on the Linux primary that owns the pool accounts" >&2
  exit 2
fi
if [[ "$(hostname -s)" == "$SEND_HOST" ]]; then
  SEND_ARGS=()
else
  SEND_ARGS=(--send-host "$SEND_HOST")
fi
mkdir -p "$LOGS" "$STATE"
(cd "$RUNTIME" && "$PYTHON" -c "import app.pool_reset_surge") >/dev/null

UNITS="$HOME/.config/systemd/user"
mkdir -p "$UNITS"
cat > "$UNITS/$UNIT.service" <<SERVICE
[Unit]
Description=agent-lb surge reset: redeem one banked Claude reset on $DAY once no Opus or Fable account is selectable

[Service]
Type=oneshot
WorkingDirectory=$RUNTIME
Environment=PATH=$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=$PYTHON -m app.pool_reset_surge --live --day $DAY --approval $APPROVAL --state $STATE ${SEND_ARGS[*]}
StandardOutput=append:$LOGS/pool-reset-surge.log
StandardError=append:$LOGS/pool-reset-surge.log
Nice=10
TimeoutStartSec=300
SERVICE
cat > "$UNITS/$UNIT.timer" <<TIMER
[Unit]
Description=Check every 5 minutes on $DAY whether the Claude pool has run dry

[Timer]
OnCalendar=$DAY *:0/5
AccuracySec=10s

[Install]
WantedBy=timers.target
TIMER
systemctl --user daemon-reload
systemctl --user enable --now "$UNIT.timer"
echo "installed $UNIT.timer every 5 min on $DAY from $RUNTIME: state $STATE ${SEND_ARGS[*]}"
