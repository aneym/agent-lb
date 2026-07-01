## Why

The menubar app derived account counts three different ways, and two of them
counted accounts the balancer will never route to: the scope-bar chips
(All/Codex/Claude) and the pool card "N accts" counted every stored account —
including subscription-canceled, deactivated, and reauth-required ones — and the
pool remaining-% aggregation summed credits of unroutable accounts, inflating
apparent capacity. Subscription-canceled accounts were simultaneously hidden from
the accounts list entirely, so the overcount was invisible. A `reauth_required`
account without a deactivation reason rendered fully healthy. This violated the
repo rule that headline totals count only authenticated, subscription-usable
accounts, and hid the operator-relevant distinction between "auth'd but
unsubscribed" and "not connected".

## What Changes

- Shared state helpers on `Account` (`APIModels.swift`): `isSubscriptionCanceled`,
  `isDisconnected` (deactivated / reauth_required / has deactivation reason),
  `isHeadlineCountable`, `isRoutable` (headline-countable and not paused).
- Scope-bar chips and pool card counts use `isHeadlineCountable`.
- Pool remaining-% / capacity / reset aggregation uses `isRoutable` only.
- Subscription-canceled accounts are shown in the accounts list as dimmed
  `unsubscribed` rows (new filter status) instead of being hidden.
- `reauth_required` rows get the re-auth treatment even with a nil
  deactivation reason.

## Impact

- Menubar headline counts and pool percentages now agree with the server's
  routable pool and dashboard overview. Unsubscribed accounts stay visible and
  clearly labeled; disconnected accounts read as needing re-auth.
- Client-only change (`clients/macos-menubar`); no server or API changes.
  Verified with `swift test` (120 tests) including new count/visibility cases.
