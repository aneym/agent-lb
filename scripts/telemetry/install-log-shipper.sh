#!/usr/bin/env bash











set -euo pipefail

LABEL="com.aneyman.agent-lb-vector"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VECTOR_CONFIG="$SCRIPT_DIR/vector.yaml"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$HOME/.agent-lb"
DATA_DIR="$LOG_DIR/vector-data"

if [[ "${1:-}" == "--uninstall" ]]; then
  launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"
  echo "Uninstalled $LABEL"
  exit 0
fi

VECTOR_BIN="${AGENT_LB_TELEMETRY_VECTOR:-$(command -v vector || true)}"
if [[ -z "$VECTOR_BIN" && -x /opt/homebrew/bin/vector ]]; then
  VECTOR_BIN=/opt/homebrew/bin/vector
fi
if [[ -z "$VECTOR_BIN" ]]; then
  echo "error: vector is required (brew tap vectordotdev/brew && brew install vector)" >&2
  exit 1
fi

INSTANCE="${AGENT_LB_TELEMETRY_INSTANCE:-}"
if [[ -z "$INSTANCE" ]]; then
  if [[ "$(hostname -s)" == *[Ss]tudio* ]]; then
    INSTANCE="studio"
  else
    INSTANCE="macbook"
  fi
fi

mkdir -p "$LOG_DIR" "$DATA_DIR" "$(dirname "$PLIST")"


INSTANCE="$INSTANCE" "$VECTOR_BIN" validate --no-environment "$VECTOR_CONFIG" >/dev/null

cat >"$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array>
    <string>$VECTOR_BIN</string>
    <string>--config</string>
    <string>$VECTOR_CONFIG</string>
  </array>
  <key>EnvironmentVariables</key><dict>
    <key>INSTANCE</key><string>$INSTANCE</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$LOG_DIR/vector.out.log</string>
  <key>StandardErrorPath</key><string>$LOG_DIR/vector.err.log</string>
</dict></plist>
PLIST_EOF

launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
sleep 1
launchctl bootstrap "gui/$UID" "$PLIST"
launchctl kickstart "gui/$UID/$LABEL" 2>/dev/null || true

deadline=$(($(date +%s) + 45))
while (($(date +%s) < deadline)); do
  if launchctl print "gui/$UID/$LABEL" 2>/dev/null | grep -q "state = running"; then
    echo "agent-lb-vector running (instance=$INSTANCE, config=$VECTOR_CONFIG)"
    exit 0
  fi
  sleep 0.5
done
echo "error: $LABEL did not reach running state within 45s — check $LOG_DIR/vector.err.log" >&2
exit 1
