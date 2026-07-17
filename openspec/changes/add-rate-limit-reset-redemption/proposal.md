# Add rate-limit reset credit redemption

## Why

OpenAI ships "saved rate limit resets" for Codex accounts (rolled out June 2026):
eligible ChatGPT accounts bank reset credits (30-day expiry) that, when redeemed,
immediately reset the account's exhausted usage windows. Redemption is currently
only reachable through the Codex CLI/ChatGPT UI per account, so when the pool
drains several accounts at once (e.g. the 2026-07-15..17 fan-out burn that zeroed
3 of 4 Codex weekly windows) the operator has to log into each account by hand to
spend a banked reset. agent-lb already holds each account's OAuth tokens and calls
the same `wham` backend for `/wham/usage`, so it can list and redeem reset credits
first-class.

The upstream contract is taken from the open-source Codex CLI
(`codex-rs/backend-client/src/client/rate_limit_resets.rs`):

- `GET {base}/backend-api/wham/rate-limit-reset-credits` → `{credits: [{id,
  reset_type, status, granted_at, expires_at, title, description}],
  available_count}`
- `POST {base}/backend-api/wham/rate-limit-reset-credits/consume` with
  `{redeem_request_id, credit_id?}` → `{code: reset | nothing_to_reset |
  no_credit | already_redeemed, windows_reset}`

## What Changes

- Add `app/core/clients/rate_limit_resets.py` with `fetch_reset_credits` and
  `consume_reset_credit`, mirroring `app/core/clients/usage.py` auth headers
  (`Authorization`, `chatgpt-account-id`), error-envelope handling, and the
  upstream-route / direct-egress opt-in guard.
- Add `AccountsService.list_rate_limit_reset_credits(account_id)` and
  `AccountsService.redeem_rate_limit_reset_credit(account_id, credit_id=None)`.
  Redemption generates a UUID `redeem_request_id` (upstream idempotency key),
  is restricted to OpenAI-provider accounts that are not `paused` /
  `deactivated` / `reauth_required`, refreshes credentials via the auth
  manager, and on success triggers an immediate usage refresh plus selection
  cache invalidation so the balancer sees the reset windows without waiting
  for the next tick.
- Add dashboard-auth admin endpoints:
  - `GET /api/accounts/{account_id}/rate-limit-reset-credits`
  - `POST /api/accounts/{account_id}/rate-limit-reset-credits/consume`
- Treat upstream `already_redeemed` as idempotent success (per Codex app-server
  docs). Surface `code` and `windows_reset` verbatim plus before/after
  primary/secondary `used_percent` so operators can verify the reset took.
- Audit-log redemptions (`account_reset_credit_redeemed`) with the upstream
  code; redemption spends a scarce banked credit, so attempts must be visible
  in the audit trail.
- Unit tests for the client payload/consume-code parsing and service
  restrictions; integration tests for both routes with mocked upstream.

## Impact

- Operators can recover a drained pool in seconds from the dashboard API
  instead of logging into each ChatGPT account.
- Single concern: no selector, proxy-pipeline, or persistence changes beyond
  the usage refresh that `UsageUpdater.force_refresh` already writes.
- Consuming a credit is intentionally explicit (admin POST, audited, no
  automation). Automatic redemption on exhaustion is out of scope — banked
  credits are scarce and operator judgment stays in the loop.
