## Why

Keep the last usable Claude account, or a Claude pool below 40% headroom, available for orchestrator-side work. Other classes should prefer another open pool without losing Claude as their final fallback.

## What Changes

- Add the declarative `policy.reserve` for `anthropic-general`.
- Compute reserve state from eligible account count and pool headroom.
- Move non-admitted classes' routable reserved-pool entries to the end of their chain.
- Expose reserve state and fallback explanations in `route pick`; preserve `route menu` formatting.

## Capabilities

### New Capabilities
- `orchestrator-reserve`: advisory pool reservation for orchestrator-side classes with a last-resort fallback.

### Modified Capabilities
None.

## Impact

Changes `clients/route` and `config/coding-agents/routing-table.json`. Does not change proxy admission, ladder routing, or reviewer selection outside the class chain. The committed reserve scenario and existing CLI/routing tests verify the change.
