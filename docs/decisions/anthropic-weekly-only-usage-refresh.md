## 2026-11-25 — Anthropic weekly-only usage refresh, gradual 429 strike decay, and tripwire parity

### Evidence

On 2026-11-25 13:44–16:27Z, the ax42 primary refused Anthropic traffic with `no_available_anthropic_accounts` (`quota_blocked=8 quota_cooldown_blocked=0 quota_candidates=1`) while weekly-only account `3950dce6` had reset at 12:00Z (`secondary=0–2%`). Commit `509949ab` (deployed 16:26:56Z) fixed the blocking gate (`_active_extra_usage_tripwire_blocks` in `anthropic_service.py`) and aligned `app/usage_export_headroom.py` (`usable_now`) via shared `extra_usage_blocked`.

Even after `509949ab`, ax42 continued hitting 429 on Anthropic `api/oauth/usage` (30 429s in 46 min after the 16:27Z restart; 29 of 30 on the 7 weekly-only accounts):

1. `UsageAccountSync._freshness_usage_entry` only inspected `window="primary"`, which stays frozen on Oct 31 / Nov 22 for weekly-only accounts; `UsageRefreshScheduler` (10 s loop) therefore polled them every 10–12 s whenever backoff cleared until Anthropic answered 429.
2. `UsageRefreshScheduler._refresh_once` ran `updater.force_refresh` on every 10 s tick whenever a stale `primary.reset_at` had `primary.recorded_at < primary.reset_at + 3`, then immediately called `updater.refresh_accounts` in the same tick (0 s back-to-back double-fetches).
3. `rate_limit_backoff.record_usage_refresh_success` did not advance `_last_rate_limit_at` when decrementing `strikes`, so multi-strike backoff collapsed to 0 after one quiet cap interval instead of stepping down one strike per quiet interval.
4. `RequestLogsRepository.add_log` called `ensure_request_id(request_id)`, permanently overwriting the background scheduler task's `_REQUEST_ID` ContextVar with `limit-warmup-...` on subsequent usage fetches.

### Decision

- Weekly-only Anthropic accounts use `window="secondary"` as their freshness entry once a post-reset `secondary` reading is newer than `primary`, and `UsageUpdater.refresh_accounts` also honors `last_successful_refresh` within `usage_refresh_interval_seconds` unless a window reset is due.
- `anthropic_reset_refresh_due` skips post-reset `force_refresh` once `secondary` or `last_successful_refresh` already ran after `primary.reset_at + 3`.
- Per-account 429 exponential backoff (`rate_limit_backoff.py`, 60 s base, 900 s cap, honoring `Retry-After` up to 3600 s) steps down at most one strike per quiet `_MAX_BACKOFF_SECONDS` interval, and `anthropic_usage.py` logs `retry_after` on failures.
- `RequestLogsRepository` resolves `request_id` without mutating `_REQUEST_ID`.
