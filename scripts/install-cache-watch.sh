#!/bin/bash



set -euo pipefail
CLIENTS="$(cd "$(dirname "$0")/.." && pwd)/clients"
BIN="$HOME/.agent-lb/bin/cache-watch"
LABEL=com.aneyman.agent-lb-cache-watch
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
install -m 755 "$CLIENTS/cache-watch" "$BIN"
install -m 755 "$CLIENTS/lb-cache" "$HOME/.agent-lb/bin/lb-cache"
install -m 644 "$CLIENTS/lb_cache_quota.py" "$HOME/.agent-lb/bin/lb_cache_quota.py"
mkdir -p "$HOME/.local/bin"
ln -sf "$HOME/.agent-lb/bin/lb-cache" "$HOME/.local/bin/lb-cache"
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>/usr/bin/python3</string><string>$BIN</string></array>
  <key>StartInterval</key><integer>600</integer>
  <key>Nice</key><integer>15</integer><key>LowPriorityIO</key><true/>
  <key>RunAtLoad</key><true/>
  <key>StandardErrorPath</key><string>$HOME/.agent-lb/cache-watch.err.log</string>
</dict></plist>
PL
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "installed $LABEL -> $BIN"
