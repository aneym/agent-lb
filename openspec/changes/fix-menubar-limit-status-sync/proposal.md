# Fix Menubar Limit Status Sync

## Why

The macOS menu bar app currently treats any future `rateLimitResetAt` value as
proof that an account is rate-limited. That makes recovered or otherwise active
accounts show as `limited` even when the backend status is `active` and routing
can select the account.

Operators also need local vendor subscription ledger updates to be visible in
the same account list without changing load-balancer routing state.

## What Changes

- Classify menu bar account status from backend account status first, using
  reset timestamps only as detail for accounts already marked blocked.
- Keep paused/deactivated routing states higher priority than stale reset
  metadata.
- Decode the account subscription ledger in the menu bar client and surface
  compact non-active subscription labels on otherwise active rows.
- Record operator-reported subscription ledger entries without pausing or
  deactivating account routing unless the backend status changes separately.

## Non-Goals

- No automatic vendor billing scraping.
- No provider-side cancellation/reactivation actions.
- No change to proxy selection semantics or load-balancer pause/reactivate
  behavior.
