#!/usr/bin/env bash
# install-front.sh — install/restart the agent-lb TCP front as a launchd agent.
#
# The front (scripts/agent-lb-front.mjs) holds the public localhost port and
# retries the upstream app port while the app restarts, so deploys stop being
# client-visible 502 windows. Install order for an existing single-port host:
#   1. move the app to the internal port (install-service plist --port), then
#   2. run this script — it binds the public port once the app releases it.
#
# Usage:
#   scripts/install-front.sh              # install or restart the front
#   scripts/install-front.sh --uninstall  # bootout + remove the plist
#
set -euo pipefail

LABEL="com.aneyman.agent-lb-front"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
FRONT_JS="$REPO_DIR/scripts/agent-lb-front.mjs"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$HOME/.agent-lb"
LISTEN_PORT="${AGENT_LB_FRONT_LISTEN_PORT:-2455}"
UPSTREAM_PORT="${AGENT_LB_FRONT_UPSTREAM_PORT:-2457}"

NODE_BIN="${AGENT_LB_FRONT_NODE:-$(command -v node || true)}"
if [[ -z "$NODE_BIN" && -x /opt/homebrew/bin/node ]]; then
  NODE_BIN=/opt/homebrew/bin/node
fi

if [[ "${1:-}" == "--uninstall" ]]; then
  launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"
  echo "Uninstalled $LABEL"
  exit 0
fi

if [[ -z "$NODE_BIN" ]]; then
  echo "error: node is required for the agent-lb front" >&2
  exit 1
fi

mkdir -p "$LOG_DIR" "$(dirname "$PLIST")"

cat >"$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array>
    <string>$NODE_BIN</string>
    <string>$FRONT_JS</string>
  </array>
  <key>EnvironmentVariables</key><dict>
    <key>AGENT_LB_FRONT_LISTEN_PORT</key><string>$LISTEN_PORT</string>
    <key>AGENT_LB_FRONT_UPSTREAM_PORT</key><string>$UPSTREAM_PORT</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$LOG_DIR/agent-lb-front.out.log</string>
  <key>StandardErrorPath</key><string>$LOG_DIR/agent-lb-front.err.log</string>
</dict></plist>
PLIST_EOF

launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
sleep 1
launchctl bootstrap "gui/$UID" "$PLIST"
launchctl kickstart "gui/$UID/$LABEL" 2>/dev/null || true

deadline=$(($(date +%s) + 15))
while (($(date +%s) < deadline)); do
  if lsof -nP -iTCP:"$LISTEN_PORT" -sTCP:LISTEN 2>/dev/null | grep -q node; then
    echo "agent-lb-front is listening on 127.0.0.1:$LISTEN_PORT -> 127.0.0.1:$UPSTREAM_PORT"
    exit 0
  fi
  sleep 0.5
done
echo "error: front did not bind :$LISTEN_PORT within 15s — check $LOG_DIR/agent-lb-front.err.log" >&2
exit 1
