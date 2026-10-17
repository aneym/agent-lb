# Plan 007: Startup migrations no longer carry the pre-Alembic bootstrap and revision-id remap

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report; do not improvise. When done, update the status row for this plan
> in `plans/README.md`, unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat 63b62f18..origin/main -- app/db/migrate.py app/db/alembic/revision_ids.py app/core/config/settings.py app/db/session.py tests/integration/test_migrations.py Makefile`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P3
- **Effort**: M
- **Risk**: HIGH (migrations are on the money path, AGENTS.md rule 8; a wrong cut can stop the service from starting)
- **Depends on**: plans/003 (Makefile edits land in order)
- **Category**: tech-debt (legacy code)
- **Planned at**: commit `63b62f18`, 2026-10-17

## Why this matters

`app/db/migrate.py` (975 lines) still carries two upgrade paths from the
upstream codex-lb history of early 2026: a bootstrap that converts the old
`schema_migrations` table into Alembic history, and an automatic remap of
pre-February-2026 Alembic revision ids (`000_base_schema` ... `014_...`) to
the current `YYYYMMDD_HHMMSS_name` ids. Every database agent-lb serves today
was created or upgraded long after both conversions. The code runs on every
startup, widens the migration module's interface (flags, settings, result
types) and has six integration tests that exist only for it.

## Current state

```python
# app/db/migrate.py:686-716 (run_startup_migrations-style entry, abbreviated)
    bootstrap_legacy: bool,
    auto_remap_legacy_revisions: bool = True,
    ...
    if bootstrap_legacy:
        bootstrap_result = _bootstrap_legacy_history(config)
    ...
    if auto_remap_legacy_revisions:
        _remap_legacy_alembic_revisions(config)
# app/db/migrate.py:734-740
    auto_remap = get_settings().database_alembic_auto_remap_enabled
        ... bootstrap_legacy=True, auto_remap_legacy_revisions=auto_remap,
# app/db/migrate.py:912-913 (CLI)
            bootstrap_legacy=not bool(args.no_bootstrap_legacy),
            auto_remap_legacy_revisions=not bool(args.no_auto_remap_legacy_revisions),
# app/core/config/settings.py:176
    database_alembic_auto_remap_enabled: bool = True
```

Legacy-only pieces in `app/db/migrate.py`: `_LEGACY_MIGRATIONS_TABLE`,
`_REQUIRED_TABLES_FOR_LEGACY_STAMP`, `LEGACY_MIGRATION_ORDER`,
`LEGACY_TO_REVISION`, `_BRANCHED_ENFORCEMENT_LEGACY_REVISION`,
`_BRANCHED_ENFORCEMENT_REPAIR_ANCESTOR`, `_LEGACY_EXTRA_COLUMNS` (check its
users first), `LegacyBootstrapResult`, `_read_legacy_migration_names`,
`_contiguous_prefix_count`, `_detect_non_contiguous_entries`,
`_missing_required_legacy_tables_for_stamp`, `_bootstrap_legacy_history`,
`_remap_legacy_alembic_revisions`, the `bootstrap` field of
`MigrationRunResult` and `has_legacy_migrations_table` of `MigrationState`
(check users in `app/db/session.py` and `app/main.py`). In
`app/db/alembic/revision_ids.py`: `OLD_TO_NEW_REVISION_MAP`,
`NEW_TO_OLD_REVISION_MAP`, `LEGACY_MIGRATION_TO_NEW_REVISION`; keep
`REVISION_ID_PATTERN`.

Two entries of `OLD_TO_NEW_REVISION_MAP` are not pre-Alembic: they map
`20260410_020000_restore_import_without_overwrite_default_false` and
`20260525_000000_merge_routing_settings_security_heads` to other revisions.
These remap revisions that were removed from the tree. Before deleting the
remap, Step 1 must prove no database sits on either id.

Integration tests that exist only for this path (`tests/integration/test_migrations.py`):
lines 99, 130, 161, 192, 211, 233 and 302 (`test_run_startup_migrations_bootstraps_legacy_history`,
`..._skips_legacy_stamp_when_required_tables...`, `..._handles_unknown_legacy_rows`,
`..._auto_remaps_legacy_alembic_revision_ids...`, `..._auto_remaps_firewall_legacy_revision_id...`,
`..._handles_legacy_schema_table_and_legacy_...`, `test_postgresql_startup_migration_auto_remap_legacy_head`).
The last one is named by path in `Makefile` `POSTGRES_PYTEST_TARGETS` (line 13).

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Worktree | `git -C /Volumes/StudioExt/repos/agent-lb worktree add /Volumes/StudioExt/repos/agent-lb-wt/cull-legacy-migrate -b cull/legacy-migrate origin/main` | exit 0 |
| Env | `sh scripts/worktree-setup.sh` | exit 0 |
| Live revision | from `~/.agent-lb/runtime/agent-lb`: `.venv/bin/python -m app.db.migrate current` (or the subcommand `--help` lists for "current"/"state") | prints one revision id matching `^\d{8}_\d{6}_` |
| Migration check | `make migration-check` | exit 0 |
| Postgres check | the Makefile `test-postgres` and `migration-check-postgres` targets against a disposable Postgres (`docker run -d --rm -e POSTGRES_USER=agent_lb -e POSTGRES_PASSWORD=agent_lb -e POSTGRES_DB=agent_lb -p 127.0.0.1::5432 postgres:16`, then `POSTGRES_TEST_DATABASE_URL=...`) | exit 0 |
| Integration | `uv run --no-sync pytest -q -n 4 --timeout=180 --timeout-method=thread tests/integration tests/e2e tests/floor` | same failure set as baseline |
| Sandbox | `scripts/lb-sandbox-check --help`, then run it per its usage against the branch | pass |

Never print the live database URL or any password. Do not pass `--db-url` with
the live URL on the command line; let the runtime read its own settings.

## Scope

**In scope**: `app/db/migrate.py`, `app/db/alembic/revision_ids.py`,
`app/core/config/settings.py` (the one setting), `app/db/session.py` and any
caller of removed result fields, `tests/integration/test_migrations.py` (delete
the seven tests), `Makefile` (`POSTGRES_PYTEST_TARGETS` line 13), any
`.env.example` line for `DATABASE_ALEMBIC_AUTO_REMAP_ENABLED`.

**Out of scope**: `app/db/alembic/versions/**` (never edit or squash
migrations here); the schema-drift and migration-policy checks;
`_ensure_alembic_version_table_capacity*`.

## Git workflow

- Branch `cull/legacy-migrate`; one commit; `check:` line naming Migration check, Postgres check and the sandbox result; `Seat:` trailer.
- Land: `git push origin HEAD:main`; `git -C /Volumes/StudioExt/repos/agent-lb pull --ff-only`; `~/.agent-lb/bin/sync-runtime.sh`; `~/.agent-lb/bin/lb-restart --reason "plan 007: drop pre-Alembic bootstrap"`.

## Steps

### Step 1: Prove every live database is past the legacy ids

On Studio run Live revision. Then check for any other agent-lb store:
`ls ~/.agent-lb/*.db ~/.agent-lb/**/store.db 2>/dev/null` and, if the federation
follower runs on another machine (`ssh book 'ls ~/.agent-lb'` if reachable),
the same command there.

**Verify**: every revision printed matches `^\d{8}_\d{6}_` and is neither
`20260410_020000_restore_import_without_overwrite_default_false` nor
`20260525_000000_merge_routing_settings_security_heads`. If any database still
has a `schema_migrations` table, an old id, or cannot be checked, STOP.

### Step 2: Baseline

Run Migration check, Postgres check and Integration; save failures to `/tmp/007-before.txt`.

**Verify**: Migration check and Postgres check exit 0.

### Step 3: Remove the legacy path

Delete the pieces listed in Current state; make the startup entry call only the
Alembic upgrade path; remove the CLI flags `--no-bootstrap-legacy` and
`--no-auto-remap-legacy-revisions`, the setting, and the seven tests plus the
Makefile line. Use `git grep -n "<name>"` before deleting each name.

**Verify**: `git grep -nE "LEGACY_MIGRATION|_bootstrap_legacy|remap_legacy|OLD_TO_NEW_REVISION_MAP|auto_remap" -- app tests Makefile` prints nothing; `uv run --no-sync ruff check .` exits 0.

### Step 4: Prove upgrades still work

**Verify**: Migration check and Postgres check exit 0; Integration has no new failures versus `/tmp/007-before.txt`; the sandbox check passes.

### Step 5: Land and restart

**Verify**: `lb-restart` completes without rollback; `curl -fsS http://127.0.0.1:2455/health/ready` returns 200; `python3 scripts/claude_cache_eval.py` passes (AGENTS.md rule 4 receipt).

## Test plan

No new tests. `make migration-check` (SQLite from empty to head) and the
Postgres targets (empty to head, drift, query plans) cover the remaining
upgrade path.

## Done criteria

- [ ] The grep in Step 3 prints nothing
- [ ] Migration check and Postgres check exit 0
- [ ] No new integration failures
- [ ] Live service healthy after restart
- [ ] `plans/README.md` row 007 updated

## STOP conditions

- Step 1 finds any database on a legacy id, with a `schema_migrations` table, or one you cannot check.
- A removed name has a caller outside `app/db/migrate.py`, `app/db/session.py` and the seven tests.
- `lb-restart` rolls back or the health gate fails.

## Maintenance notes

- A database restored from a backup older than February 2026 can no longer be upgraded in place; restore through the version at `63b62f18` first.
- Reviewer: read the new startup entry end to end; it should be "ensure version-table capacity, upgrade to head, check drift".

LOC removed (estimate): ~450 (about 300 in `migrate.py` and `revision_ids.py`, about 150 in tests).
