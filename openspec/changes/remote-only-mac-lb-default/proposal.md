# Remote-only Macs default to Studio's agent-lb

## Why

Book runs no local agent-lb. Its desktop proxy LaunchAgent and `claude-lb-launch` both defaulted to `http://127.0.0.1:2455`, so every Claude Code turn on Book failed with ECONNREFUSED until the plist and the Rails host job were hand-edited (2026-10-15).

## What Changes

- On macOS, when `~/Library/LaunchAgents/com.aneyman.agent-lb.plist` (or the `CLAUDE_LB_LAUNCHD_LABEL` plist) is absent and `CLAUDE_LB_LOCAL_URL` is unset, `claude-lb-launch` defaults its LB to `https://studio.tailf266ac.ts.net:2455` and skips the local candidate.
- `scripts/install-claude-desktop-proxy.sh` defaults its upstream the same way.
- Explicit `CLAUDE_LB_BASE_URL`, `CLAUDE_LB_LOCAL_URL` and `CLAUDE_DESKTOP_PROXY_UPSTREAM` still win. Linux hosts and Macs with a local LB keep the current defaults.

## Impact

- Affected code: `clients/claude-lb-launch`, `scripts/install-claude-desktop-proxy.sh`, and their unit tests.
