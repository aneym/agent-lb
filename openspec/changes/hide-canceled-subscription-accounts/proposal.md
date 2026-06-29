# Change: Hide canceled subscription accounts outside account management

## Why

Operators can cancel vendor subscriptions before agent-lb detects that the
account no longer works. Once a local subscription ledger records an account as
`canceled`, that account should stop influencing routing and operational totals
while still remaining visible in the Accounts page for audit and reactivation.

## What Changes

- Treat local subscription status `canceled` as non-routable and not
  subscription-usable for headline/dashboard surfaces.
- Keep canceled accounts visible in `GET /api/accounts` and the Accounts page.
- Add an operator action to recheck a canceled account; if the upstream account
  works again, mark the local subscription ledger `active` so it re-enters
  routing and operational summaries.

## Impact

- Backend routing and summary calculations filter canceled subscriptions.
- Dashboard Accounts detail gains a "Check sub" action for canceled accounts.
- Existing pause/reactivate account state remains separate from vendor
  subscription state.
