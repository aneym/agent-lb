# Cap automatic reset-credit spend and add operator reset controls

## Why

`add-auto-reset-credit-redemption` made the pool self-heal from full Codex
exhaustion, bounded only by a 15-minute cooldown. That cooldown bounds a burst,
not a day: a pool that keeps re-exhausting could spend the whole bank in an
afternoon, and banked credits are scarce (one granted per account every few
days, 30-day expiry). The operator's rule (2026-09-10) is a daily allowance —
one automatic reset per rolling 24h, plus one more if the pool hits the limit
again the same day — so the pool still recovers twice unattended but cannot
drain the bank.

The same session surfaced the other half: redemption was reachable only through
the dashboard API by hand (`POST /api/accounts/{id}/rate-limit-reset-credits/consume`
via curl). The operator sees exhausted accounts and banked credit counts in the
dashboard and the menubar, and needs to spend one from either surface —
including while the automatic allowance is spent, which is exactly when a human
decides the pool must come back now.

## What Changes

- **Daily allowance.** Add `reset_credit_auto_redeem_max_per_day` (default 2)
  and lower `reset_credit_auto_redeem_cooldown_seconds` back to its 900 s role
  as *minimum spacing* between two automatic redemptions. The allowance is
  counted from the durable attempt ledger via
  `ResetCreditAttemptsRepository.count_applied_since`, restricted to
  `trigger="auto"` rows with a non-null `applied_at` — so a restart cannot lift
  it, an `auto` attempt that came back `nothing_to_reset` does not spend it, and
  expiry-sweep / operator redemptions never count against it. `max_per_day <= 0`
  disables automatic exhaustion redemption, matching the `enabled` kill switch.
- **Dashboard control.** `AccountActions` gains a "Reset limits (N)" button for
  OpenAI accounts that are neither paused nor awaiting operator recovery,
  disabled when no credit is banked, behind a `ConfirmDialog` that states the
  spend is non-refundable. Backed by a `resetCreditMutation` that reports the
  upstream `code` verbatim — `nothing_to_reset` and `no_credit` are surfaced as
  such, not as success — and invalidates the account queries so the row's new
  status and credit count land immediately.
- **Menubar control.** The existing `⟲ N` chip becomes the tap target on rows
  where a reset would buy capacity, with an equivalent "Reset Rate Limits (N
  banked)" context-menu item and inline in-flight/failed feedback. Eligibility
  moves out of the view into `Account.canRedeemResetCredit` so it is unit-tested:
  Codex provider, banked credit > 0, routable (not paused, disconnected or
  unsubscribed), and limit-blocked. `AccountResetCreditConsumeResponse.didReset`
  reads the upstream verdict rather than the HTTP status.
- No change to the exhaustion rule itself, the redemption service path, the
  expiry sweep, or the API contract — both clients call the endpoints that
  `add-rate-limit-reset-redemption` already shipped.

## Impact

- Unattended credit spend is bounded at two per rolling 24h while the pool can
  still self-heal from a genuine same-day re-exhaustion.
- The operator can spend a banked credit from the dashboard or the menubar in
  one click, including after the automatic allowance is spent — the manual path
  is deliberately uncapped, since it is a human decision on a visible count.
- Menubar chip gains an action but no new row height, so `PanelLayout` stays
  exact.
