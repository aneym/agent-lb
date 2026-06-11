# Tasks: default-enable-account-limit-warmup

- [x] 1. Update OpenSpec deltas for the new per-account warm-up opt-in default.
- [x] 2. Flip `Account.limit_warmup_enabled` ORM default and server default to true.
- [x] 3. Add Alembic migration altering the `accounts.limit_warmup_enabled` server default (no backfill of existing rows), with downgrade.
- [x] 4. Add regression coverage: new accounts persist with warm-up enabled; existing rows keep their stored value across the migration.
- [x] 5. Run targeted test suites and `openspec validate --specs`.
