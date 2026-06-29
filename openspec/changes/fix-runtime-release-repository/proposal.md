# Change: Point runtime update checks at the public release repository

## Why

The public README, package metadata, screenshots, and release handoff all point
operators to `aneym/agent-lb`, but the runtime version endpoint still checks
and links to the old upstream repository. That makes the dashboard footer's
update indicator misleading for the public fork.

## What Changes

- Change runtime version lookup to use the canonical public release repository,
  `aneym/agent-lb`.
- Return `https://github.com/aneym/agent-lb/releases/latest` as the dashboard
  release URL.
- Update runtime API tests and client fixtures that document the version
  response contract.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `outbound-http-clients`: runtime release lookup source and release URL are
  made explicit for the public repository.

## Impact

- Runtime version service GitHub lookup URL.
- Runtime version response schema default.
- Dashboard and menu bar client fixtures that consume `/api/runtime/version`.
