## Why

When an operator resubscribes or upgrades an account at the vendor, the menubar
keeps rendering it stale (e.g. `unsubscribed`) until the next background pulse
runs. There was no way to force re-verification from the panel: the server
already exposes `POST /api/accounts/{id}/subscription/check` (re-verifies the
subscription ledger) and `POST /api/accounts/{id}/probe` (wakes the upstream
rate limiter and refreshes usage), but the menubar client called neither.

## What Changes

- `APIClient` gains `checkSubscription(_:)` and `probeAccount(_:)` POST methods
  with decoded responses (`AccountSubscriptionCheckResponse`,
  `AccountProbeResponse` in `APIModels.swift`). They run on a dedicated
  URLSession with a 30 s request timeout because both endpoints round-trip the
  upstream vendor — the default 3 s dashboard-read session would kill them.
- Pure endpoint-choice function `AccountRefreshAction.action(for:)`:
  subscription-canceled rows → `checkSubscription`; paused/disconnected rows →
  no action (the server rejects their probes with 409 `account_not_probable`);
  everything else → `probe`.
- `AccountRow` gains a subtle per-row refresh control: a 16 pt
  `arrow.clockwise` slot left of the trailing status text, revealed via opacity
  on row hover (always visible on unsubscribed rows), spinner while in flight,
  checkmark/exclamation hint for 2 s after completion, then the existing
  account-list refresh renders the new state. No modals, no layout jumps — the
  slot is fixed-size and only enters the layout for refreshable rows.
- `AppState.refresh(accountId:via:)` follows the pause/reactivate contract:
  returns false on any failure (including 409) so the row hints inline, and
  refreshes accounts only on success.

## Impact

- Operators can force on-demand re-verification from the menubar right after a
  vendor-side resubscribe/upgrade instead of waiting for a background pulse.
- Client-only change (`clients/macos-menubar`); no server or API changes.
  Verified with `swift build` and `swift test` (134 tests) including new
  endpoint-choice and response-decoding cases.
