## Why

Workflows on several machines need one versioned answer to which seat, model, effort and box to run. The rules version is the agent-lb commit.

## What Changes

- Store seats and implement escalation as routing data.
- Resolve `route seat` and `route seats` at a pinned commit of the policy clone, falling back to the installed table with source `installed` when APPLIED is unavailable.
- Manage four additional seat definitions.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `coding-agent-routing`: Versioned seat resolution.
- `deployment-installation`: Manage every seat definition.

## Impact

Every machine reinstalls the policy; a separate sync job keeps installations current. No LB service change or restart.
