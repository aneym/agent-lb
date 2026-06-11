#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

make test                                      # 1. unit tests green
make bundle                                    # 2. bundle assembles + signs
codesign --verify --deep AgentLB.app           # 3. signature valid
plutil -lint AgentLB.app/Contents/Info.plist   # 4. plist well-formed

curl -fsS --max-time 2 http://127.0.0.1:2455/health >/dev/null \
  || echo "WARN: agent-lb service down — app should show Stopped state"

open AgentLB.app                               # 5. launch
sleep 3
pgrep -x AgentLB >/dev/null                    # 6. process alive (LSUIElement: no Dock icon expected)

# 7. visual check: status item is in the menu bar (manual confirm via screenshot)
screencapture -x /tmp/agentlb-menubar.png
echo "Inspect /tmp/agentlb-menubar.png top-right for the AgentLB status icon."

# 8. teardown
pkill -x AgentLB || true
echo "E2E PASS"

# Manual checklist (one-time per release):
# - Open popover → ready state renders real pool data from /api/usage/summary
# - Stop service: launchctl unload ~/Library/LaunchAgents/com.aneyman.agent-lb.plist
#   → Reopen popover → Stopped state shows + Start button
#   → Click Start → service recovers, popover transitions to ready state
# - Right-click an account row → Pause → row flips to ⏸ pause glyph
#   → confirm dashboard /accounts page agrees
# - System Preferences → Accessibility → Reduce Transparency ON
#   → Reopen popover → renders on opaque .regularMaterial, no layout breakage
# - Footer "Copy URL" → paste → confirms http://127.0.0.1:2455 (or configured baseURL)
