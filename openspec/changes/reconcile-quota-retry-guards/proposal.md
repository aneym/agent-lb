# Reconcile quota retry guards and Opus observations

## Why
A bounded persisted refusal can expire while its raw in-memory cooldown still excludes the account. Opus status also reads legacy top-model quota keys instead of the Opus-specific keys the router persists.

## Changes
Normalize both retry guards from the same refusal marker. Observe the exact Opus thinking/non-thinking scope, and label exhausted Opus telemetry unknown rather than claiming a full-window routing prohibition.

## Non-goals
No new probe API, cleared generic refusal from Haiku/non-thinking success, backoff policy change, permissions change, or automatic restart. Existing live refusal intervals up to one hour remain intact.
