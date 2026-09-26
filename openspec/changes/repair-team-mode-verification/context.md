# Verification repair evidence

All changes in this follow-up are isolated to `/Volumes/StudioExt/repos/agent-lb-worktrees/team-mode` and remain uncommitted. The runtime and main checkout were not modified by this follow-up. The Team feature deployed earlier remains healthy.

## Repairs

The migration tool now recognizes a complete pre-existing reset-credit table before stamping its schema-only revision. It compares the frozen historical table shape, including types, nullability, primary key and uniqueness. Regression tests reject malformed tables and verify preserved data. Existing migration files are unchanged.

SQLite reflection misreports the descending text expression in `idx_logs_session_time`. The drift checker now checks the physical index using `PRAGMA index_list/index_xinfo` before ignoring that false diff. Wrong order, direction, collation, uniqueness, partial-index shape and missing indexes still fail tests.

Settings updates clear account response caches when additional-quota routing policies change. The existing integration test proves the immediately following account response contains the new policy.

Stale tests now use current mapper arguments, selection persistence keyword arguments and return values, a controlled prune clock, current model/provider/quota catalogs, and an actual external-prefix installer fixture. No tests were skipped or deleted to obtain passing results.

The June public-release snapshot test now targets its two release changes rather than imposing their exact pending tasks on every future feature. The exact release assertions remain. The three documented runtime fixes without spec deltas now have normative deltas derived from their existing implementations. Their unrelated incomplete retention task remains unchecked.

Eleven application files were formatted. Python AST comparison against HEAD confirmed all eleven changes are formatting-only.

## Completed checks

- Team contract tests: 47 passed, exit 0, including new window-boundary, cached member reassignment, and key expiration tests.
- Migration unit/integration suites plus Team migration: 63 passed, three PostgreSQL-only skips, exit 0.
- Eight repaired fixture suites: 290 passed, three existing skips, exit 0.
- Quota-policy integration suite: five passed, exit 0.
- Public-release documentation suite: 80 passed, exit 0.
- `uv run ruff check app clients`: exit 0.
- `uv run ruff format --check app`: exit 0, 461 files already formatted.
- `npm run typecheck`, `npm run lint`, `npm test -- --run`, `npm run build`: exit 0; 95 frontend test files and 634 tests passed.
- Fresh isolated SQLite upgrade: single head `20260918_000000_add_team_members`, migration policy clean, no schema drift.
- Strict OpenSpec validation of the Team change, maintenance change and three restored deltas: exit 0.

The exact full `uv run pytest -q` command completed with exit 0: **4,344 passed, 43 skipped, four warnings in 280.49 seconds**. The skips are four PostgreSQL-only tests, 36 Helm-dependent checks, and three existing concurrency tests superseded by per-account locking. They are not claimed as executed.

Final logs are under `evidence/`. All required local contract commands are now green. This supersedes the earlier 32-failure result in the deployment closeout; it does not erase that historical evidence. No repairs from this follow-up have been deployed or copied onto the unrelated main working tree.
