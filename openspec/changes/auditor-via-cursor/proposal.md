## Why

Non-Claude mechanical seats are currently skipped when their Sonnet auditor's Claude pool is critical. Sonnet review can instead run through Cursor's other-models pool, preserving the Claude orchestrator reserve.

## What Changes

- Add optional `policy.auditor_via` alternate auditor routes.
- Retain seats when their exhausted or critical auditor pool has a healthy alternate.
- Explain the alternate in JSON audit metadata and the text audit line.
- Ignore malformed alternate configuration without changing existing skip behavior.

## Capabilities

### New Capabilities
- `auditor-via-cursor`: alternate auditor transport when the primary auditor pool is unavailable.

### Modified Capabilities
None.

## Impact

Changes `clients/route`, the routing table, and route regression coverage. Does not change author/vendor eligibility or other class-chain filters.
