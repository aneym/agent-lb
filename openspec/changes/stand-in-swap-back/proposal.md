# Stand in and swap back

## Why
Tagged orchestrator and lane-tab main threads should continue through the existing GPT bridge while the Anthropic pool is unavailable, without silently changing Claude reviewers or untagged sessions.

## What Changes
- Read intent and lane tags once when the per-session shim starts; leave the shared desktop shim unchanged.
- On eligible Anthropic failures before the first response chunk, release the reservation and use the live routing policy's stand-in model.
- Persist active stand-ins and the newest 50 returned sessions, and expose them through the dashboard-authenticated pools endpoint.
- Try Anthropic on every turn and clear the active record on its first success.

## Impact
Affected specifications: anthropic-messages-compat, routing-pools.
Affected code: proxy stand-in store and Messages handler, pools route, session launcher.
No system-block mutation, migration, routing-table edit, or runtime restart. Mid-stream failures and stand-in failures retain existing behavior.
