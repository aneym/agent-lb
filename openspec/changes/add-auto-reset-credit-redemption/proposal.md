# Add automatic reset-credit redemption on full pool exhaustion

## Why

`add-rate-limit-reset-redemption` gave operators manual endpoints to list and
redeem banked OpenAI "saved rate limit resets". The operator's standing policy
(2026-07-17) is: a banked reset should only ever be spent when **all** Codex
accounts are used up — while any account still has capacity, credits stay
banked. Waiting for a human to notice full exhaustion defeats the point (the
pool is down until someone acts), and per the repo's own doctrine, threshold
checks like this belong in deterministic code, not in an agent's judgment.

## What Changes

- Add a leader-elected background scheduler
  (`app/modules/accounts/reset_credit_scheduler.py`, started/stopped in
  `main.py` alongside the existing schedulers) that periodically evaluates the
  OpenAI pool and auto-redeems **at most one** banked reset credit when — and
  only when — every subscription-usable OpenAI account in serving statuses
  (`active`, `rate_limited`, `quota_exceeded`) is exhausted (no `active`
  account remains).
- Candidate policy: exhausted accounts are considered `quota_exceeded` first
  (weekly-dead accounts recover slowest), then by earliest-expiring available
  credit (use-it-or-lose-it). The first successful redemption ends the sweep.
- Redemption reuses `AccountsService.redeem_rate_limit_reset_credit`, so the
  post-redeem usage refresh and selection-cache invalidation apply and the
  restored account rejoins routing immediately.
- A global cooldown (`reset_credit_auto_redeem_cooldown_seconds`, default 900)
  bounds credit spend: after a successful auto-redemption the scheduler will
  not redeem again until the cooldown elapses, even if the pool is somehow
  still exhausted. The cooldown is in-memory; a restart during a still
  fully-exhausted pool may redeem once more, which matches the rule's intent.
- Settings (env-configurable, no dashboard plumbing):
  `reset_credit_auto_redeem_enabled` (default `true`),
  `reset_credit_auto_redeem_interval_seconds` (default 60),
  `reset_credit_auto_redeem_cooldown_seconds` (default 900).
- Auto-redemptions are audit-logged as `account_reset_credit_redeemed` with
  `trigger: auto`.

## Impact

- The pool self-heals from full Codex exhaustion within one scheduler tick
  when a banked credit exists, without spending credits while any capacity
  remains — encoding the operator's "only when all accounts are used" rule.
- Paused / deactivated / reauth-required / subscription-canceled accounts are
  excluded from both the exhaustion check and redemption candidates: they
  cannot serve traffic, so they must neither block nor trigger redemption.
- Failure isolation: upstream errors on one candidate fall through to the
  next; a fully failed sweep retries on the next tick without cooldown.
