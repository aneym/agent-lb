#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

make test
make bundle
codesign --verify --deep AgentLB.app
plutil -lint AgentLB.app/Contents/Info.plist

curl -fsS --max-time 2 http://127.0.0.1:2455/health >/dev/null \
  || echo "WARN: agent-lb service down — app should show Stopped state"

open AgentLB.app
sleep 3
pgrep -x AgentLB >/dev/null


screencapture -x /tmp/agentlb-menubar.png
echo "Inspect /tmp/agentlb-menubar.png top-right for the AgentLB status icon."


pkill -x AgentLB || true
echo "E2E PASS"
