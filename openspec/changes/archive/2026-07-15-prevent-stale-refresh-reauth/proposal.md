## Why

Concurrent OAuth refresh attempts can race after a provider rotates a refresh token. A stale database view can then miss the newer credential version and incorrectly mark an otherwise usable account as `reauth_required`. This removed valid accounts from routing and caused unnecessary manual reauthentication.

## What Changes

- Re-read the current account credential state from the database before recording a permanent authentication failure.
- Persist rotated token sets with compare-and-swap semantics against the exact refresh-token version used for the provider exchange.
- Treat a lost compare-and-swap where the database already contains newer token material as convergence on the newer account state.
- Add regression coverage for stale identity-map reads, concurrent token writes, and permanent refresh failures that lose a race to a successful refresh.

## Capabilities

### New Capabilities

- `oauth-refresh-safety`: Defines concurrency and persistence guarantees for OAuth credential refreshes.

### Modified Capabilities

None.

## Impact

The change affects the account authentication manager, account repository persistence contract, and refresh-related tests. It does not change the public API, database schema, or dependencies.
