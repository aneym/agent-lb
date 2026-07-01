# Change: Remove cancellation-pending subscription status

## Why

The local subscription ledger is operator-maintained metadata. Agent-lb does not
automatically know whether a vendor subscription is pending cancellation, so a
dedicated `cancel_pending` status can imply billing knowledge the system does
not have.

## What Changes

- Remove `cancel_pending` from the account subscription ledger status contract.
- Keep active-until timestamps available as separate ledger metadata.
- Normalize legacy stored `cancel_pending` rows to `active` in `/api/accounts`
  so old data does not break the dashboard or resurface the misleading label.
- Update operator guidance, dashboard schemas, tests, and OpenSpec text to use
  `active`, `pause_pending`, `paused`, or `canceled`.

## Impact

- Backend: subscription update requests reject `cancel_pending`; account-list
  responses map legacy rows to `active`.
- Frontend: the Accounts subscription dropdown no longer offers
  `cancel_pending`, and active-until dates no longer depend on that status.
- Routing: only `canceled` remains the local subscription status that excludes an
  account from selection.
