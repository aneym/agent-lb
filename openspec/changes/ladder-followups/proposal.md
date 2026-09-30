## Why

Rollback inspection must reject broken inactive ladders. Cursor dispatch should not suggest retired models.

## What Changes

- Validate all configured ladders with the existing routing validator.
- Filter Cursor closest hints with route's retired-model rule.

## Capabilities

### Modified Capabilities

- `coding-agent-routing`

## Impact

`clients/route`, `clients/seat`, routing contract tests. Missing account data remains non-blocking. No credentials or server schema changes.

## Removed from Scope

- Item 3: Cursor preflight early stop and lazy failover probing are out of scope, split into a later slice.
- Item 4: Composer-first exploration is removed; exploration keeps its existing ladder order.

- Item 5: pool refill inspection is split into the pool-refills change.
