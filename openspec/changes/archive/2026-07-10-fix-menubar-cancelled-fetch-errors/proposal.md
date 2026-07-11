## Why

Closing or rapidly reopening the macOS menu-bar popover cancels its in-flight
section fetches. Those cancellations are currently stored as real pool,
accounts, and recent-section failures, so healthy cached data renders alongside
misleading "couldn't load — retry" rows.

## What Changes

- Treat task and URL cancellation as a neutral fetch outcome that does not
  mutate section error state.
- Clear a prior section error whenever either the foreground or silent
  background fetch for that section succeeds.
- Preserve stale-data-plus-retry rendering for genuine completed failures.

## Capabilities

### Modified Capabilities

- `macos-menubar`: section error state reflects completed fetch outcomes rather
  than popover lifecycle cancellation.

## Impact

- Affected code: `clients/macos-menubar/Sources/AgentLB/AppState.swift` and its
  focused XCTest coverage.
- No backend API, layout metric, or retry-row presentation changes.

