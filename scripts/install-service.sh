#!/usr/bin/env bash
# install-service.sh — install/restart agent-lb as a macOS launchd user agent.
#
# Resolves the repo from this script's location, generates a LaunchAgent plist
# (label com.agent-lb) pointing at the project venv binary, and bootstraps it.
# Guards against clobbering a server already listening on port 2455 under a
# different label.
#
# Usage:
#   scripts/install-service.sh            # install or restart the service
#   scripts/install-service.sh --print    # print the generated plist, no changes
#   scripts/install-service.sh --uninstall  # bootout + remove the plist
#
set -euo pipefail

LABEL="com.agent-lb"
PORT=2455
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_DIR="$(realpath "$REPO_DIR")"
BIN="$REPO_DIR/.venv/bin/agent-lb"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$HOME/.agent-lb"

gen_plist() {
  cat <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$BIN</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$REPO_DIR</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$LOG_DIR/agent-lb.out.log</string>
    <key>StandardErrorPath</key>
    <string>$LOG_DIR/agent-lb.err.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
</dict>
</plist>
PLIST
}

label_loaded() {
  launchctl print "gui/$UID/$LABEL" >/dev/null 2>&1
}

case "${1:-}" in
  --print)
    gen_plist
    exit 0
    ;;
  --uninstall)
    launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"
    echo "Uninstalled $LABEL"
    exit 0
    ;;
  "")
    ;;
  *)
    echo "usage: $0 [--print | --uninstall]" >&2
    exit 2
    ;;
esac

if [[ ! -x "$BIN" ]]; then
  echo "error: $BIN not found — run \`uv sync\` first" >&2
  exit 1
fi

# Port-conflict guard: refuse to touch a server we don't own.
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1 && ! label_loaded; then
  echo "error: port $PORT is in use by another process — is agent-lb already running under a different service name?" >&2
  exit 1
fi

mkdir -p "$LOG_DIR"
mkdir -p "$(dirname "$PLIST")"

if label_loaded; then
  echo "Booting out existing $LABEL..."
  launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
  # Immediate re-bootstrap after bootout fails with error 5 on macOS.
  sleep 5
fi

gen_plist >"$PLIST"
launchctl bootstrap "gui/$UID" "$PLIST"

deadline=$(($(date +%s) + 30))
while (($(date +%s) < deadline)); do
  if health="$(curl -fsS "http://127.0.0.1:$PORT/health" 2>/dev/null)"; then
    echo "agent-lb is up: $health"
    exit 0
  fi
  sleep 1
done

echo "error: agent-lb did not become healthy within 30s — check $LOG_DIR/agent-lb.err.log" >&2
exit 1
