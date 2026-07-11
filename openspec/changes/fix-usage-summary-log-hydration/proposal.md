# Stop /api/usage from hydrating the full request-log window per poll

## Why

A SIGUSR2 stack dump captured during the 2026-07-11 studio freezes showed the
event loop inside `_logs_for_accounts` serving `/api/usage`:
`get_usage_summary` loaded every RequestLog row in the secondary (7-day)
window — >700k ORM rows — and filtered them in Python, on the loop, on every
menubar/dashboard poll. Combined with GC pressure from the result sets this
froze the proxy for tens of seconds per poll.

## What Changes

- The log-derived aggregates (`UsageMetricsSummary`, `UsageCostSummary`) for
  the usage summary are computed at most once per 60s per
  (provider, window, account-set) key and served from an in-process TTL
  cache between recomputations (`_log_window_metrics`).
- Recomputations use account-filtered SQL aggregates for both unscoped and
  provider-scoped summaries; they do not hydrate request-log ORM rows.

## Impact

- Affected specs: usage-refresh-policy (usage summary derivation)
- Affected code: `app/modules/usage/service.py`
- Staleness: menubar metrics/cost may lag up to 60s. Window summaries
  (remaining %) are unaffected — they come from usage rows, not logs.
