## Why

Alex requested Muse Code installation and agent-lb integration on 2026-10-13. Meta publishes the CLI, but agent-lb has no Muse resolver, account pool or seat adapter.

## What Changes

- Record the official distribution, authentication and headless execution contracts.
- Resolve `muse-latest` to an explicit Muse model pin without changing any class ladder.
- Add Muse to the provider CLI guard so an unprovisioned command cannot use Alex's personal sign-in.
- Prepare the remaining installer, account, pool and seat integration for verification against the pinned binary.

## Capabilities

### New Capabilities

- `muse-code-seat`: pinned model resolution, provisioned authentication and receipt-backed Muse dispatch.

### Modified Capabilities

None.

## Impact

`clients/route`, `config/coding-agents/routing-table.json`, the provider CLI guard and focused subprocess tests. Completion also requires `clients/seat`, pool readback, policy installation, a seat definition and the open-factory installer and seat runner. Those remain pending while the binary and named open-factory worktree are unavailable.
