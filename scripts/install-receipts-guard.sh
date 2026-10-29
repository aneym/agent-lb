#!/bin/bash
set -euo pipefail
RUNTIME="${AGENT_LB_RUNTIME_DIR:-$HOME/.agent-lb/runtime/agent-lb}"
PRIMARY_HOST="${AGENT_LB_PRIMARY_HOST:-}"
TUNNEL_PORT=15432
mkdir -p "$HOME/.agent-lb"

if [[ "$(uname)" == Darwin ]]; then
  [[ -n "$PRIMARY_HOST" ]] || { echo "set AGENT_LB_PRIMARY_HOST to the ssh host that runs the primary database" >&2; exit 2; }
  AGENTS="$HOME/Library/LaunchAgents"
  TUNNEL=com.agentlb.primary-db-tunnel
  GUARD=com.agentlb.receipts-guard
  cat > "$AGENTS/$TUNNEL.plist" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$TUNNEL</string>
  <key>ProgramArguments</key><array>
    <string>/usr/bin/ssh</string><string>-N</string>
    <string>-o</string><string>BatchMode=yes</string>
    <string>-o</string><string>ExitOnForwardFailure=yes</string>
    <string>-o</string><string>ServerAliveInterval=15</string>
    <string>-o</string><string>ServerAliveCountMax=3</string>
    <string>-o</string><string>ControlMaster=no</string>
    <string>-o</string><string>ControlPath=none</string>
    <string>-L</string><string>127.0.0.1:$TUNNEL_PORT:127.0.0.1:5432</string>
    <string>$PRIMARY_HOST</string>
  </array>
  <key>KeepAlive</key><true/>
  <key>RunAtLoad</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardErrorPath</key><string>$HOME/.agent-lb/primary-db-tunnel.err.log</string>
</dict></plist>
PL
  cat > "$AGENTS/$GUARD.plist" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$GUARD</string>
  <key>ProgramArguments</key><array>
    <string>$RUNTIME/.venv/bin/python</string><string>-m</string><string>app.receipts_guard</string>
  </array>
  <key>WorkingDirectory</key><string>$RUNTIME</string>
  <key>EnvironmentVariables</key><dict>
    <key>PATH</key><string>$HOME/.local/bin:$HOME/.agent-lb/bin:/opt/homebrew/bin:/usr/bin:/bin</string>
  </dict>
  <key>StartInterval</key><integer>900</integer>
  <key>Nice</key><integer>15</integer>
  <key>StandardOutPath</key><string>$HOME/.agent-lb/receipts-guard.out.log</string>
  <key>StandardErrorPath</key><string>$HOME/.agent-lb/receipts-guard.err.log</string>
</dict></plist>
PL
  umask 077
  printf 'postgresql://agent_lb:agent_lb@127.0.0.1:%s/agent_lb\n' "$TUNNEL_PORT" > "$HOME/.agent-lb/primary-database.url"
  for label in "$TUNNEL" "$GUARD"; do
    launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$AGENTS/$label.plist"
  done
  echo "installed $TUNNEL to $PRIMARY_HOST and $GUARD every 15 min"
  exit 0
fi

UNITS="$HOME/.config/systemd/user"
mkdir -p "$UNITS"
cat > "$UNITS/agent-lb-receipts-guard.service" <<UNIT
[Unit]
Description=agent-lb receipts guard: post to organizer when request_logs receipts stop for an hour

[Service]
Type=oneshot
WorkingDirectory=$RUNTIME
Environment=PATH=$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=$RUNTIME/.venv/bin/python -m app.receipts_guard
StandardOutput=append:$HOME/.agent-lb/receipts-guard.out.log
StandardError=append:$HOME/.agent-lb/receipts-guard.err.log
Nice=15
UNIT
cat > "$UNITS/agent-lb-receipts-guard.timer" <<UNIT
[Unit]
Description=Run the agent-lb receipts guard every 15 minutes

[Timer]
OnBootSec=5min
OnUnitActiveSec=15min

[Install]
WantedBy=timers.target
UNIT
systemctl --user daemon-reload
systemctl --user enable --now agent-lb-receipts-guard.timer
echo "installed agent-lb-receipts-guard.timer every 15 min"
