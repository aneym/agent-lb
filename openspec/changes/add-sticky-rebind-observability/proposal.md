# Add Sticky-Session Rebind Observability

## Why

Anthropic sticky sessions (durable pins) can be silently rebound to a different
account by several selection paths: rate-limit failover after a grace-period
retry fails, budget-pressure reallocation once a pin crosses the configured
threshold, the fable burn-first sticky drain, and a pinned account going
paused/deactivated or leaving the pool. Every such rebind is a full Anthropic
prompt-cache bust — the org-scoped cache for that conversation is abandoned and
its input tokens are re-billed uncached on the new account.

Today nothing records these rebinds. `StickySessionsRepository.upsert` writes
unconditionally and bumps `updated_at` on every same-account touch, so a rebind
is indistinguishable from a refresh. `request_logs.session_id` is `NULL` for all
Anthropic rows, and no audit action captures the transition. Rebind frequency and
cause are therefore unmeasurable (verified 2026-07-07 against the live database),
which makes the prompt-cache cost of account switching invisible to operators.

## What Changes

- When sticky selection resolves an **existing** pin to a **different** account
  than the one previously stored, the system records a `sticky_session_rebound`
  audit event capturing the hashed sticky key, sticky kind, old account id, new
  account id, the triggering reason, and a timestamp. A same-account touch and a
  first-time pin creation record nothing.
- The reason is threaded from the branch that actually forced the rebind — one of
  `rate_limited_failover`, `budget_pressure`, `burn_first_drain`,
  `account_unavailable`, or `other` — not inferred after the fact.
- Recording is **fail-open**: any error persisting the event is swallowed after a
  single log line and never fails or delays the proxied request.
- Events reuse the existing `audit_logs` table (new `action` value only) so they
  are queryable via SQL on the live Postgres and via the existing audit-log
  listing API/dashboard. No schema change or migration is introduced.
- The raw affinity key is never persisted — it is hashed (SHA-256) before it
  reaches the audit table, consistent with the observability rule that raw
  affinity keys must not be logged.

## Impact

- Affected specs: `sticky-session-operations` (rebind events are recorded with a
  reason), `proxy-runtime-observability` (the audit event and its redaction
  contract).
- Affected code: `app/modules/proxy/load_balancer.py`
  (`LoadBalancer._select_with_stickiness` threads the reason and calls the new
  fail-open `_record_sticky_rebind`).
- Behavior change: a new audit action appears for genuine rebinds only. No schema
  or migration change, no new table, no new UI. The selection result and latency
  are unchanged on both the success and the recording-failure path.
