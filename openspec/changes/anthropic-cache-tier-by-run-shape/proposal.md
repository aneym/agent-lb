## Why
The r1-quota-climb audit of 48 hours of agent-lb rows (2026-11-11 19:42Z to 2026-11-13 19:42Z) found two cache tier faults. Long-lived agent sessions that send five-minute breakpoints and wake every ten minutes rewrote their whole context on every wake: 197 requests wrote 55M tokens after a 5 to 60 minute gap, and product back offices on Fable ran at a 66% hit rate. One-shot `claude -p` runs (entrypoint `sdk-cli`) never came back after five minutes in 1,222 of 1,222 runs, yet wrote 41.6M tokens at the one-hour price.

## What Changes
- Claude Code sessions that are not one-shot runs write every breakpoint at the one-hour tier, whatever tier the client asked for (`anthropic_session_cache_1h_enabled`).
- One-shot runs (`sdk-cli` entrypoint) write every breakpoint at the five-minute tier (`anthropic_one_shot_cache_5m_enabled`).
- Every breakpoint in a request gets the same tier, including breakpoints inside nested tool-result content, so tier order stays valid.
- With both flags off, the existing helper rule (`anthropic_helper_cache_1h_enabled`) applies unchanged.

## Capabilities
### Modified Capabilities
- `anthropic-messages-compat`: the proxy sets the cache tier from the run's shape.

## Impact
One proxy policy module and two settings. Only the `ttl` value inside existing `cache_control` objects changes; no block is added, removed or reordered, and the billing marker stays first.
