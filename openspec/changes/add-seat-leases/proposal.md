## Why

Agent work also runs on other machines. The owner made agent-lb the single source for every agent credential, including Cursor and Devin. Neither CLI can go through the LB, so an account is leased per attempt and its outcome comes back to the same state `seat run` keeps.

## What Changes

- Add `seat lease` and `seat release` for per-attempt bundles and outcomes.
- Count active leases as use when selecting accounts and show lease counts in `seat accounts`.
- Lock all state writers and apply only their own changes to fresh state.

## Capabilities

### New Capabilities

- `cli-seat-accounts`: Lease and release CLI-only seat accounts.

### Modified Capabilities

None.

## Impact

Only `clients/seat` changes. The LB reads the state unchanged and needs no restart. The policy install puts the new `seat` on every machine.
