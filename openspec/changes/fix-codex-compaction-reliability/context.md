# Context: fix-codex-compaction-reliability

## Root cause

A context-compaction request is a single large (~150–200k-token) `/v1/responses`
streaming call. Its connect + serialize + send can consume the 60s first-event
window, so the upstream produces zero events and the bridge raises
`bridge_first_event_timeout`. That timeout was recorded against the account's
in-memory `error_count` (`_handle_stream_error` → `record_error`). At
`error_count >= 3` the account enters 30–300s backoff exclusion
(`app/core/balancer/logic.py`), so a handful of compaction timeouts drained the
whole codex pool to a spurious `no_accounts` 503 — invisible to the dashboard
ACTIVE status (the drain is in-memory). Observed: bursts of ~130 gpt-5.5
`no_accounts`/hour, each logged with `excluded_count=0` (pool emptied before any
filter). The compaction failover loop also tried only 2 accounts, below a
typical pool.

## Fix

1. `bridge_first_event_timeout` is account-neutral (`_is_account_neutral_error_code`):
   `_handle_stream_error` no longer records account health for it. The
   per-bridge-key cooldown + sticky-mapping delete in
   `_quarantine_http_bridge_first_event_account` remain the steering mechanism,
   so failover is unchanged; only the global pool-draining penalty is removed.
2. `_COMPACT_MAX_ACCOUNT_ATTEMPTS` 2 → 4 so a single critical compaction call
   fails over across the codex pool before returning `no_accounts`.

## Tradeoffs and tracked follow-ups (deliberately out of scope here)

- **Continuity/file-pinned broken-account latency.** A genuinely-broken account
  whose _only_ failure mode is a first-event timeout is no longer demoted via
  this path. For a _continuation_ request (`previous_response_id` set), in-request
  failover is gated off and the per-bridge-key cooldown is bypassed for the
  continuity-preferred account, so such a request can waste ~60s per call without
  demoting the account. This does **not** affect compaction (a fresh `store:false`
  request that still gets in-request failover + cooldown), and genuine account
  faults (connection/5xx/auth) still demote. Follow-up option: a bounded
  _consecutive_ first-event-timeout counter per account that demotes only after N
  consecutive timeouts with no intervening success — distinguishing occasional
  upstream slowness (the compaction case) from a persistently-broken account
  without re-introducing the pool drain.
- **First-event deadline reset on retry.** The bridge resets the 60s first-event
  deadline on a failover retry, so a single request can run ~120s — longer than a
  90s client budget, making the client time out first. Follow-up option: cap the
  total first-event time across retries to one budget instead of resetting per
  attempt. (The Hermes client also now patiently retries + has a deterministic
  fallback, so this is not the dominant compaction failure.)
- **Anthropic usage 429 over-polling storm** (separate concern): the usage poller
  has 401/403 cooldowns but no 429 backoff, so a rate-limited Anthropic account
  re-fetches every poll. Strictly Anthropic-scoped; does not affect codex
  eligibility. Track as a separate cleanup.
