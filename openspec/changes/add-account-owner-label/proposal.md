# Add account owner labels to the menubar

## Why

Federated accounts appear beside local accounts in the macOS menubar. Their raw owner instance ID is not a useful label for pushed accounts, and a missing distinction makes it hard to tell which LB refreshes them.

## What Changes

- `GET /api/accounts` adds an optional `ownerLabel`: the push source name if bound, otherwise the remote owner's instance ID. Local accounts expose null.
- The menubar identifies shared rows with a neutral chip alongside the plan chip; privacy mode hides the source identity.
- Account routing and ownership are unchanged.
