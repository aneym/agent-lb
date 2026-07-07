# Tasks

- [x] Thread a `rebind_reason` through `_select_with_stickiness` set from the
      actual triggering branch: `rate_limited_failover` (grace-failed durable
      pin / far-away rate-limit reallocation), `budget_pressure` (threshold
      reallocation), `burn_first_drain` (fable burn-first sticky drain),
      `account_unavailable` (pinned account permanently down or gone from the
      pool), else `other`.
- [x] Add `LoadBalancer._record_sticky_rebind` that writes a
      `sticky_session_rebound` audit event via the existing `AuditService`,
      hashing the sticky key (SHA-256) so raw affinity keys never reach the
      table, and swallowing any error after a log line (fail-open).
- [x] Record the event only at the fallback-upsert site, and only when an
      existing pin resolves to a different account (`existing` set and
      `new_account_id != existing`); same-account touches and first pins record
      nothing.
- [x] Reuse the `audit_logs` table (new `action` value only) — no schema change,
      no migration, no new table.
- [x] Regression: rebind via failover records reason `rate_limited_failover`.
- [x] Regression: rebind via budget pressure records reason `budget_pressure`.
- [x] Regression: rebind via burn-first drain records reason `burn_first_drain`.
- [x] Regression: rebind via paused pin and via pin-left-pool record
      `account_unavailable`.
- [x] Regression: same-account repeat selection and first-pin creation record
      nothing.
- [x] Regression: a simulated recording failure does not fail selection and the
      rebind still persists.
- [x] Run focused tests (`tests/unit/test_select_with_stickiness.py`,
      `tests/unit/test_load_balancer.py`) and `ruff check app clients`.
- [x] Validate the OpenSpec change strictly and validate all specs.
