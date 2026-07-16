# Fix pool-wide block from transient Anthropic rate-limit cooldowns

## Why

Anthropic's live request-rate limiter answers 429 with an unparseable body
and no reset headers. agent-lb records those as the 60s default requested-
quota cooldown (`_ANTHROPIC_DEFAULT_COOLDOWN_SECONDS`). On a small pool the
cooldowns overlap: every account is "cooling down", `_select_account` raises
the pool-wide 429 (`All Anthropic accounts are cooling down for quota …`),
and the next client retry re-triggers a fresh upstream 429 — a self-
perpetuating block that survives restarts because cooldowns are DB rows.
Reported by a 2-account deployment (2026-07-16); reproduced on the 5-account
studio box, where ~14k proxy-path 429s/week and ~34k anthropic_top cooldown
writes are masked only by pool depth (some account is always eligible).

## What Changes

- Add a transient-cooldown last resort to `_provider_quota_eligibility`:
  when no account is eligible and the remaining candidates are blocked only
  by a requested-quota cooldown whose reset is within
  `_ANTHROPIC_TRANSIENT_COOLDOWN_BYPASS_HORIZON_SECONDS` (120s), re-admit
  those candidates and let upstream be the authority (a genuine limit
  answers 429 and rewrites a fresh bounded cooldown).
- All transient candidates are re-admitted, not just the earliest reset, so
  the caller's failover loop can exclude a just-429'd account and try the
  next.
- Accounts with active primary/secondary window exhaustion, an active
  extra-usage tripwire cooldown, an unbounded (None) reset, or a reset
  beyond the horizon stay blocked — header-derived long resets remain
  authoritative, and the paid extra-usage last resort is unchanged.
- Emit `anthropic_transient_cooldown_bypass` WARNING when the bypass
  triggers so operators can see the pool is running on upstream authority.

## Capabilities

### Modified Capabilities

- `account-routing`: pool-wide requested-quota cooldown blocks become a
  last-resort readmission when every blocking cooldown is near-reset
  (transient rate-limit shaped) rather than a terminal 429.

## Impact

- Affected code: `app/modules/proxy/anthropic_service.py`
- Affected tests: `tests/unit/test_load_balancer.py` (three new eligibility
  tests); existing far-reset and extra-usage behavior covered by the
  untouched unit + integration suites.
- Operational impact: small pools (1–2 accounts) under upstream request-rate
  429 pressure now degrade to per-request upstream retries instead of a
  permanent local 429; upstream traffic is bounded by client request rate.
