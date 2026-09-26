#!/bin/bash
# Install clients/cache-watch as a 10-minute launchd job, with clients/lb-cache
# beside it (cache-watch reads lb-cache first). Both live under $HOME because
# launchd jobs cannot read the external volume.
set -euo pipefail
SRC="$(cd "$(dirname "$0")/.." && pwd)/clients/cache-watch"
BIN="$HOME/.agent-lb/bin/cache-watch"
LABEL=com.aneyman.agent-lb-cache-watch
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
install -m 755 "$SRC" "$BIN"
install -m 755 "$(dirname "$SRC")/lb-cache" "$HOME/.agent-lb/bin/lb-cache"
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>/usr/bin/python3</string><string>$BIN</string></array>
  <key>StartInterval</key><integer>600</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardErrorPath</key><string>$HOME/.agent-lb/cache-watch.err.log</string>
</dict></plist>
PL
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "installed $LABEL -> $BIN"
