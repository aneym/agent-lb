# Proposal: default-enable-account-limit-warmup

## Why

Every account added to this deployment gets limit warm-up turned on manually right
after OAuth completes. The per-account opt-in defaulting to disabled adds a manual
dashboard/API step per account and risks a newly added account silently missing
warm-up seeding. The global warm-up toggle already gates all warm-up traffic, so a
per-account default of enabled stays safe on fresh installs.

## What Changes

- Newly created accounts default to `limit_warmup_enabled = true` (ORM default and
  database server default).
- Existing account rows are not backfilled; their current opt-in state is preserved.
- The global limit warm-up toggle remains disabled by default, so fresh installs
  still send no warm-up traffic until an operator enables it.

## Impact

- `usage-refresh-policy`: per-account opt-in default flips to enabled for new
  accounts; global gating unchanged.
- `database-migrations`: new Alembic revision altering the `accounts.limit_warmup_enabled`
  server default from false to true.
