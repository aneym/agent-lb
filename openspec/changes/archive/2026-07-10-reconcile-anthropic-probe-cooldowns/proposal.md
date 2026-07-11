## Why

Anthropic model-quota cooldown rows can outlive the upstream condition they describe and exclude a healthy Fable account until an obsolete reset horizon. This occurred when a fresh account showed zero Fable usage and passed an account-pinned Fable probe, while a two-day-old `anthropic_top_thinking` cooldown still blocked high-effort routing.

## What Changes

- Reconcile active Fable model-quota cooldowns during the existing account pulse with tiny account-pinned, quota-shaped Fable probes.
- Clear `anthropic_top` only after a successful plain-Fable probe and `anthropic_top_thinking` only after a successful adaptive-thinking Fable probe.
- Preserve cooldowns after inconclusive or refused probes, and preserve unrelated account, subscription, usage, and quota state.
- Add regression coverage for stale-cooldown recovery and non-success safety.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `account-routing`: Healthy Anthropic accounts recover automatically from stale Fable model-quota cooldowns after decisive account-pinned evidence.

## Impact

- Account pulse orchestration and Anthropic probe request construction.
- Additional-usage history receives append-only zero-percent reconciliation rows for contradicted cooldowns.
- No public API, schema, migration, or frontend contract changes.
