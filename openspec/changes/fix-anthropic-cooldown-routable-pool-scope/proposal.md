# Fix Anthropic Cooldown Diagnostics to Scope the Routable Pool

## Why

The Anthropic model-quota eligibility prefilter and the no-account selection
diagnostics counted every stored provider account, including
canceled-subscription, deactivated, paused, and reauth-required rows that the
load balancer can never select. When all genuinely usable accounts were cooling
down on a model quota, those unusable rows were reported as "remaining
candidates," producing a contradictory error (`N accounts exist ... none
selectable ... M accounts remained after the prefilter`) and a generic `503`
instead of the precise cooldown `429` with retry metadata.

Observed incident: 6 stored Anthropic rows (3 usable, 2 canceled subscriptions,
1 deactivated). All 3 usable accounts hit the `anthropic_top_thinking` 5-hour
limit at once. Because the prefilter considered all 6 rows, the 3 unusable rows
survived the cooldown prefilter, were handed to the selector, were dropped as
unselectable, and the proxy returned a `503` with `selection_error_code=None`.
The client saw "6 Anthropic accounts exist ... 3 accounts remained after the
prefilter," which reads like a routing bug even though the routable pool was
simply exhausted. This also violates the repo rule that headline account totals
count only subscription-usable accounts.

## What Changes

- Promote `selectable_accounts` to a public, single-source-of-truth helper in the
  proxy load balancer (was the private `_selectable_accounts`).
- Scope the Anthropic model-quota eligibility prefilter to the routable pool, so
  unusable rows are neither counted as blocked nor passed to the selector as
  candidates. When every routable account is cooling down, the prefilter yields
  zero candidates with a positive blocked count, so the proxy returns the native
  cooldown `429` with `Retry-After` / unified-reset metadata.
- Scope the selection-failure diagnostics headline count and status summary to
  the routable pool, and report stored-but-unusable rows in a separately labeled
  note instead of inflating the total or listing them as remaining candidates.

## Impact

- Affected specs: `account-routing`.
- Affected code: `app/modules/proxy/load_balancer.py`,
  `app/modules/proxy/anthropic_service.py`.
- Behavior change: in the all-routable-cooling case the proxy returns a native
  rate-limit `429` (with retry metadata) instead of a generic `503`. Error
  message wording for Anthropic selection failures changes (routable-only counts
  plus a labeled unusable-rows note).
