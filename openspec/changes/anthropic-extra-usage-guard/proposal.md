## Why

Anthropic accounts with "extra usage" enabled never return 429 when their
subscription window exhausts — upstream silently keeps serving and bills
metered usage credits (observed live: $38 burned on one account in a day).
agent-lb is blind to this twice over:

1. The usage client discards the `extra_usage` block from `/api/oauth/usage`
   (`extra="ignore"`), so the dashboard cannot show credit burn and routing
   cannot distinguish a credit-billing account from a healthy one.
2. Between usage refreshes (~60s), a session pinned to an account crossing
   100% keeps getting 200s — every such request bills real dollars before the
   refresh flips the account to `rate_limited`. There is no 429 to trigger the
   cooldown/failover machinery.

Operator policy: never route pool traffic onto usage credits; subscription
capacity elsewhere (or waiting for a reset) is always preferable.

## What Changes

- `anthropic_usage` client parses `extra_usage` (`is_enabled`, `used_credits`,
  `monthly_limit`, `utilization`, `currency`, `decimal_places`) and surfaces it
  on account rows via the existing credits fields (`creditsHas` = enabled,
  `creditsBalance` = remaining extra-usage dollars, `creditsUnlimited` = false)
  — no schema migration.
- Hard selection gate: an Anthropic account whose primary window utilization is
  at/above 100 is excluded from selection regardless of upstream still
  answering 200 on credits. New setting
  `ANTHROPIC_ROUTE_TO_EXTRA_USAGE` (default false): when false, such accounts
  stay excluded even when the pool is otherwise exhausted (selection returns
  the existing 429 + `retryAt` envelope so the launcher waits for reset);
  when true, they become last-resort candidates only.
- Response-level tripwire to close the refresh lag: on every proxied Anthropic
  response, inspect the rate-limit status headers
  (`anthropic-ratelimit-unified-status` family — implementer must confirm the
  exact header names from a live subscription-covered response); when the
  headers show the unified limit exhausted/overage, record the same quota
  cooldown a 429 would, so at most one request per account per window can bill
  credits before rotation.

## Impact

- Pool traffic cannot silently burn metered credits; worst case one request
  per account per window (tripwire), zero once the refresh catches up.
- Pool-wide exhaustion surfaces as the existing wait-for-reset flow instead of
  open-ended spend.
- Credit burn becomes visible per account in `/api/accounts` (menubar/dashboard
  pick it up for free via the existing credits fields).
- Affected: `app/core/clients/anthropic_usage.py`, usage refresh mapping,
  `app/modules/proxy/anthropic_service.py` (eligibility + response tripwire),
  `app/core/config/settings.py`, tests.
