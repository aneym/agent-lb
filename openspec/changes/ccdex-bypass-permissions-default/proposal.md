# ccdex defaults to bypassPermissions

## Why

ccdex sessions inherited the operator's `defaultMode: auto` permission setting, and the auto-mode classifier denied background subagents' filesystem access — every fan-out stalled on permission retries (observed 2026-07-11, owner request). ccdex is the owner's trusted local harness; it should launch with permissions bypassed by default.

## What Changes

- In ccdex mode the launcher injects `--permission-mode bypassPermissions` unless the operator explicitly passes `--permission-mode` or `--dangerously-skip-permissions`.
- `CC_PERMISSION_MODE` is ignored in ccdex mode (consistent with `CC_EFFORT_LEVEL`); regular `cc` behavior is unchanged.
- The ccdex banner names the mode (`bypass perms`).

## Capabilities

### Modified Capabilities

- `claude-harness-codex`: default permission mode at launch.

## Impact

`clients/claude-lb-launch` and its unit tests. No server, routing, or protocol changes.
