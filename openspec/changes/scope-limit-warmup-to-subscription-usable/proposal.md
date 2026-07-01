## Why

Limit warm-up candidates are gated only on `account.status == active`. An account whose
subscription ledger is `canceled` (e.g. upstream returns 403 "OAuth authentication is
currently not allowed for this organization") keeps `status = active` — the balancer
already excludes it from selection via `is_subscription_usable`, so it never receives
live traffic, but the warm-up scheduler kept sending it primer requests that fail with
`upstream_403` every cycle. Observed in production: 17 consecutive failed warm-up
attempts each on two ledger-canceled Anthropic accounts (2026-06-13 → 2026-07-01),
producing wasted upstream requests and recurring error-log noise.

## What Changes

- `_account_is_safe_candidate` in `app/modules/limit_warmup/service.py` additionally
  requires `is_subscription_usable(account)` (subscription ledger status not
  `canceled`), matching the balancer's routable-pool rule
  (`selectable_accounts`).
- Regression test: a subscription-canceled account produces no warm-up sends, no
  persisted attempts, and no request-log rows through `run_after_usage_refresh`.

## Impact

- Warm-up target selection now agrees with the routable pool: accounts the balancer
  will never route to no longer receive synthetic warm-up traffic.
- No schema, API, or dashboard changes. Recovery detection for canceled accounts is
  unchanged (manual `POST /api/accounts/{id}/subscription/check` flips the ledger
  back to `active`, which re-enables warm-up automatically).
