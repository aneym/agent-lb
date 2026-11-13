## Why
An active Anthropic account with recovered quota is reported blocked solely by a stale future account reset marker. Selection ignores the marker for active accounts but carries it from runtime back to storage after reset recovery clears it.

## What Changes
- Ignore an active account reset marker as a standalone advisory status block.
- Do not seed active selection state with obsolete DB/runtime account reset markers.
- Preserve quota readings, blocked statuses, runtime cooldowns, provider rules and optimistic persistence.

## Capabilities
### Modified Capabilities
- `advisory-status-cli`: active status uses quota observations, not a leftover account reset marker.
- `account-routing`: active selection does not resurrect obsolete account reset markers.

## Impact
Two Python source files. No API shape, account operation, credentials or deployment changes.
