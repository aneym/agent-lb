# Raise agent-lb scheduling priority under host load

## Why

Under heavy host load, the Python service's event loop can stall for 5–24 seconds, making clients report agent-lb offline even though launchd still has the agent loaded. A LaunchAgent probe on this Mac confirmed that launchd honors negative Nice values for agents. The live service plist has already been adjusted by hand; installers should retain this scheduling priority on reinstall. Brief failed menu-bar health checks should not erase the last known healthy status before the service has had a chance to respond.

## What Changes

- Generate service, TCP front, and Claude Desktop proxy LaunchAgents with `ProcessType = Interactive` and `Nice = -10` by default.
- Preserve an existing integer `Nice` or string `ProcessType` in the service plist as an operator override.
- Define a 45-second grace window for menu-bar status after the last successful health check; the menu-bar behavior is specified here for a separate implementation.

## Capabilities

### Modified Capabilities

- `deployment-installation`: LaunchAgents receive scheduling priority defaults, with service overrides preserved.
- `macos-menubar`: transient failed health checks keep the last known running or degraded status within the grace window.

## Impact

- Affected code: `scripts/install-service.sh`, `scripts/install-front.sh`, `scripts/install-claude-desktop-proxy.sh`, and installer tests.
- Affected specs: `deployment-installation`, `macos-menubar`.
- This change does not itself alter the menu-bar client; implementing its grace window remains a separate task.
