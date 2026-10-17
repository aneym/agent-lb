# Plan 003: Unit tests are gone; only kept-floor guards remain, in `tests/floor/`

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report; do not improvise. When done, update the status row for this plan
> in `plans/README.md`, unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat 63b62f18..origin/main -- tests Makefile scripts/local_ci.py .pre-commit-config.yaml frontend clients/macos-menubar clients/open-factory/tests scripts/tests .github/CONTRIBUTING.md docs/local-ci.md pyproject.toml`
> New or changed files under `tests/unit/` since `63b62f18` are expected (the
> repo is busy); classify each new one with the rules in Step 2. Any change to
> a KEEP file or to the three integration files that import unit helpers is a
> STOP condition until you re-read them.

## Status

- **Priority**: P1
- **Effort**: L
- **Risk**: MED
- **Depends on**: plans/002 (both edit `Makefile` and `scripts/local_ci.py`; run 002 first)
- **Category**: tests
- **Planned at**: commit `63b62f18`, 2026-10-17

## Why this matters

The owner, 2026-10-17: "get rid of unit tests completely". agent-lb carries
about 128,000 lines of Python unit tests (`tests/unit/`, 296 files), 8,800
lines of frontend Vitest tests, 5,900 lines of Swift tests for the menubar
app, and small suites elsewhere. Integration and end-to-end suites
(`tests/integration/`, 129 files, and `tests/e2e/`) stay, along with the
sandbox checks (`scripts/lb-sandbox-check`, `scripts/lb-spend-cap-check`)
and the live cache check (`scripts/claude_cache_eval.py`).

Two exceptions are not negotiable:

1. **Kept-floor guards stay.** The owner's kept floors are: never print a
   password or secret, no message to a real person without his yes, no spend
   past a cap, no destructive data without an approval record. A test that
   guards one of these is kept (moved to `tests/floor/`).
2. **Tests a gate runs by path stay or the gate is rewired in the same change.**
   Here: `Makefile` `test-unit`, `CI_TARGETS`, `scripts/local_ci.py`
   `BASELINE_TARGETS`, `.pre-commit-config.yaml` `frontend-test`.

## Current state

### KEEP list (move each to `tests/floor/`, unchanged except path fixes)

| Current path | Kept floor it guards |
|--------------|----------------------|
| `tests/unit/test_api_keys_service.py` | spend cap: `validate_key` enforces key limits (`test_validate_key_checks_expiry_and_limit`, `test_validate_key_multi_limit_all_must_pass`) |
| `tests/unit/test_structured_logging.py` | secret exposure: `test_redact_log_value_masks_keyed_secrets_and_bearer_tokens`, basic-auth masking, errors logged without secrets |
| `tests/unit/test_conversation_archive.py` | secret exposure: redacted archive records, operator-only file mode |
| `tests/unit/test_no_meta_keychain_reads.py` | secret exposure: no tracked code reads the Meta keychain items |
| `tests/unit/test_policy_core.py` | all floors: the installed policy core keeps the kept-floor sentences (credential floor, spend cap, relayed-yes list) |
| `tests/unit/test_hook_dispatcher.py` | all floors: floor guards fail closed through the dispatcher (`test_a_floor_guard_that_fails_refuses_the_call`, `test_missing_registry_fails_closed`) |
| `tests/test_provider_cli_guard.py` | destructive data: `config/coding-agents/hooks/dangerous-command-guard.sh` |
| `tests/test_railway_write_guard.py` | secret custody writes: `config/coding-agents/hooks/railway-vars-guard.sh` |
| `tests/test_plutil_guard.py` | destructive data: `config/coding-agents/hooks/plutil-guard.sh` (a dispatcher `FLOOR_SCRIPTS` entry) |

`tests/unit/test_hook_dispatcher.py:280` reads `tests/fixtures/hook-dispatch/link-cli-guard.sh`; keep that fixture.
The three root files compute their target as `Path(__file__).resolve().parents[1] / ...`;
after the move to `tests/floor/` that must become `parents[2]`. The six
`tests/unit/` files already use `parents[2]` (same depth), so they need no change.

### MOVE list (these are integration tests by boundary: ASGI app plus DB through `async_client`/`app_instance`, from `tests/conftest.py`)

Move to `tests/integration/` unchanged (fix `parents[...]` only if the file computes a repo path):

- `tests/test_eval_web_tool_strip.py` (marked `pytest.mark.integration`)
- `tests/test_loopback_trust.py` (marked `pytest.mark.integration`)
- `tests/test_request_logs_options_api.py` (marked `pytest.mark.integration`; also named by `Makefile` `test-unit`)
- `tests/unit/test_anthropic_proxy_api.py`, `tests/unit/test_audit_trail.py`, `tests/unit/test_compat_balance_exhausted.py`, `tests/unit/test_cursor_budget_pools.py`, `tests/unit/test_stand_in_widening.py` (use `async_client`)

### Helpers integration tests import from a unit file

```python
# tests/integration/test_route_pick_leases.py:10
from tests.unit.test_route_ladder import SCRIPT
# tests/integration/test_route_reservations.py:19
from tests.unit.test_route_ladder import SCRIPT, setup, table_copy
# tests/integration/test_seat_run_reservation.py:17-18
from tests.unit.test_route_ladder import SCRIPT as ROUTE
from tests.unit.test_route_ladder import setup, table_copy
```

`tests/unit/test_route_ladder.py` lines 12-60 define `REPO`, `SCRIPT`,
`CANONICAL_TABLE`, `CURSOR_LIST`, `DEVIN_LIST`, `setup(tmp_path, *, claude_eligible=3, codex_low=True)`
and `table_copy(tmp_path, name, edit)`. Copy exactly those definitions (and the
imports they need) into a new `tests/integration/route_support.py`, fix
`REPO = Path(__file__).resolve().parents[2]`, and change the three imports to
`from tests.integration.route_support import ...`.

### DELETE list

- every other file under `tests/unit/` (including `__init__.py` once the directory is empty)
- `tests/agentlb/` (all 8 files; not run by any Make target)
- root files `tests/test_codex_local_launch.py`, `tests/test_focus_steal_check.py`, `tests/test_hooks_off_volume.py`, `tests/test_install_policy_box_sync.py`, `tests/test_no_new_comments.py`, `tests/test_route_eval_gate.py`, `tests/test_route_outcomes_wiring.py`, `tests/test_wide_scan_guard.py`
- `scripts/tests/` (3 shell tests)
- `clients/open-factory/tests/` (1 file)
- frontend: every `frontend/src/**/*.test.ts` and `frontend/src/**/*.test.tsx`, and the directory `frontend/src/test/`. Do NOT delete `frontend/src/features/auth/components/bootstrap-setup-screen.tsx` or `frontend/src/features/settings/components/password-setup-dialog.tsx` (product code whose names contain "setup").
- menubar: `clients/macos-menubar/Tests/` and the `.testTarget(...)` block in `clients/macos-menubar/Package.swift` (line 15), and the `test:` target in `clients/macos-menubar/Makefile` (lines 44-45 and its `.PHONY` entry).
- `tests/fixtures/*` files that no remaining test, app or script references after the deletion (Step 7 finds them).

### Gate wiring today

```make
# Makefile:72-74
test-unit: frontend-build
	uv sync --dev --frozen
	PYTHONFAULTHANDLER=1 uv run pytest $(PYTEST_ARGS) tests/unit tests/test_request_logs_options_api.py
# Makefile:53-54
frontend-test: frontend-install
	cd frontend && bun run test:coverage
# Makefile:185
ci-fast: lint typecheck frontend-test test-unit package
# Makefile:207-208
menubar-test:
	cd clients/macos-menubar && swift test
```

`scripts/local_ci.py` `BASELINE_TARGETS` (lines 40-61) must equal `make -s ci-targets` exactly or every local CI run fails.
`.pre-commit-config.yaml` lines 29-35 run `make frontend-test` at pre-push.
`frontend/package.json` scripts `test` and `test:coverage`; devDependencies
`@testing-library/jest-dom`, `@testing-library/react`, `@testing-library/user-event`,
`@vitest/coverage-v8`, `jsdom`, `msw`, `vitest`; `frontend/vite.config.ts` line 64
has a `test:` block with `setupFiles: "./src/test/setup.ts"`.

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Worktree | `git -C /Volumes/StudioExt/repos/agent-lb worktree add /Volumes/StudioExt/repos/agent-lb-wt/cull-unit -b cull/unit-tests origin/main` | exit 0 |
| Env | `sh scripts/worktree-setup.sh` | exit 0 |
| Floor suite | `uv run --no-sync pytest -q tests/floor` | all pass |
| Integration | `uv run --no-sync pytest -q -n 4 --timeout=180 --timeout-method=thread tests/integration tests/e2e` | same failure set as baseline |
| Lint | `uv run --no-sync ruff check . && uv run --no-sync ruff format --check .` | exit 0 |
| Gate parity | `python3 -c "import subprocess,sys; sys.path.insert(0,'scripts'); import local_ci; t=set(subprocess.check_output(['make','-s','ci-targets'],text=True).split()); assert t==local_ci.BASELINE_TARGETS, t^local_ci.BASELINE_TARGETS; print('ok')"` | `ok` |
| Frontend | `make frontend-lint frontend-typecheck frontend-build` | exit 0 |
| Menubar | `make menubar-build` | exit 0 |
| Routing smoke | `python3 config/coding-agents/verify-routing --source-only` | exit 0 |
| Launcher smoke | `CLAUDE_LB_DRY_RUN=1 python3 clients/claude-lb-launch -p hi` | exit 0 |

Pytest and vitest are routed off Studio by a hook; if it says so, run them via
`seat-run --box auto --name <name> -- bash -lc "cd <worktree> && <command>"`.
`make menubar-build` needs macOS; run it on Studio.

## Scope

**In scope**: everything in the KEEP, MOVE and DELETE lists; new
`tests/floor/` and `tests/integration/route_support.py`; `Makefile`;
`scripts/local_ci.py`; `.pre-commit-config.yaml`; `frontend/package.json`,
`frontend/bun.lock`, `frontend/vite.config.ts`; `clients/macos-menubar/Package.swift`,
`clients/macos-menubar/Makefile`; `pyproject.toml` (`[tool.pytest.ini_options]`
markers may drop `unit`); `.github/CONTRIBUTING.md`, `.github/PULL_REQUEST_TEMPLATE.md`,
`docs/local-ci.md` (lines that tell people to run `tests/unit` or `make test-unit`).

**Out of scope**:
- `tests/integration/**` except the three import lines and the new helper file; `tests/e2e/**`; `tests/load/**` (k6 load scripts, not unit tests); `tests/conftest.py`.
- Production code. Test-only seams in `app/` and `clients/route` are removed by plan 004, not here.
- `docs/route-cli.md` (plan 004 edits it).
- `clients/open-factory/harbor/README.md`: it documents eval task images built from historical commits, which keep their own tests.

## Git workflow

- Branch `cull/unit-tests`. Commit in three logical units: (1) move KEEP and MOVE files and the route helper, (2) delete Python unit tests and rewire Make/local CI, (3) delete frontend and menubar tests.
- Each body has a `check:` line and a `Seat:` trailer.
- Land: `git push origin HEAD:main` (fast-forward only).

## Steps

### Step 1: Baseline

Run the Integration command on the untouched worktree; save
`grep -E "^(FAILED|ERROR)" | sort > /tmp/003-before.txt`. Also run
`uv run --no-sync pytest -q <the 9 KEEP files>` and note the result.

**Verify**: both files exist; the KEEP files pass (if one fails on `origin/main`, record it, do not fix it, and continue).

### Step 2: Classify anything new

List `git ls-files tests/unit tests/test_*.py`. Any file not named in this plan
(added after `63b62f18`) is classified like this: it uses `async_client` or
`app_instance` → MOVE; it asserts one of the four kept floors (grep for
`redact`, `secret`, `password`, `keychain`, `spend`, `cost_usd`, `limit`,
`floor`, `destructive`, `approval`, `outbound`) and the assertion is the
file's main subject → KEEP; else DELETE. Write the decision per file in the commit body.

**Verify**: every listed file has a decision.

### Step 3: Create `tests/floor/` and the route helper

`git mv` the nine KEEP files into `tests/floor/` (add an empty `tests/floor/__init__.py`).
Change `parents[1]` to `parents[2]` in the three former root files. Create
`tests/integration/route_support.py` and repoint the three integration imports.
`git mv` the MOVE files into `tests/integration/`.

**Verify**: Floor suite passes; `uv run --no-sync pytest -q tests/integration/test_route_pick_leases.py tests/integration/test_route_reservations.py tests/integration/test_seat_run_reservation.py` gives the same result as on the baseline.

### Step 4: Delete the Python unit tests

`git rm -r` the DELETE list for Python (`tests/unit`, `tests/agentlb`, the
eight root files, `scripts/tests`, `clients/open-factory/tests`).

**Verify**: `git ls-files tests/unit tests/agentlb scripts/tests clients/open-factory/tests` prints nothing; `git grep -n "tests\.unit\|tests/unit" -- tests` prints nothing.

### Step 5: Rewire the gates

In `Makefile`: replace `test-unit` with

```make
test-floor:
	uv sync --dev --frozen
	PYTHONFAULTHANDLER=1 uv run pytest $(PYTEST_ARGS) tests/floor
```

update `.PHONY`, the `help` text, `ci-fast` (`lint typecheck test-floor package`),
and `CI_TARGETS` (replace `test-unit` with `test-floor`; remove `frontend-test`
and `menubar-test`). Delete the `frontend-test` and `menubar-test` targets.
In `scripts/local_ci.py` make `BASELINE_TARGETS` match. In
`.pre-commit-config.yaml` delete the `frontend-test` hook. Remove `swift` from
`REQUIRED_TOOLS` only if `menubar-build` no longer needs it (it does: keep it).

**Verify**: Gate parity prints `ok`; `make -s ci-targets | grep -c "test-unit\|frontend-test\|menubar-test"` prints `0`.

### Step 6: Delete frontend and menubar tests

Delete the frontend test files and `frontend/src/test/`, the `test:` block in
`vite.config.ts`, the `test` and `test:coverage` scripts, and the seven test
devDependencies; run `cd frontend && bun install` to update `bun.lock`.
Before deleting `msw`, run `rg -n "msw|src/test" frontend/src --glob '!**/*.test.*'`;
any hit outside `frontend/src/test/` is a STOP. Delete the menubar `Tests/`,
the `.testTarget` block and the menubar Makefile `test` target.

**Verify**: Frontend command exits 0; Menubar command exits 0; `git ls-files frontend | grep -c "\.test\.ts"` prints `0`.

### Step 7: Drop orphaned fixtures

For each `git ls-files tests/fixtures` path `P`, run
`git grep -lF "${P#tests/}" -- tests app scripts clients config`. Delete each fixture with no hit.

**Verify**: `tests/fixtures/hook-dispatch/link-cli-guard.sh` and `tests/fixtures/route/routing-table.json` still exist; Floor suite and Integration still pass as before.

### Step 8: Docs

Edit `.github/CONTRIBUTING.md`, `.github/PULL_REQUEST_TEMPLATE.md` and
`docs/local-ci.md` so no line tells anyone to run `tests/unit` or
`make test-unit`; the Windows tier keeps only the `create_app()` import smoke.

**Verify**: `git grep -n "tests/unit\|test-unit" -- .github docs Makefile AGENTS.md` prints nothing.

### Step 9: Full comparison and land

Run Integration (save `/tmp/003-after.txt`), Floor suite, Lint, Routing smoke,
Launcher smoke.

**Verify**: `comm -13 /tmp/003-before.txt /tmp/003-after.txt` is empty except for the moved files' own pre-existing failures (compare them by test name); all smokes exit 0. Push.

## Test plan

No new tests. Replacement coverage per area, all already existing:

| Area the deleted tests covered | Check that now carries it |
|-------------------------------|---------------------------|
| `app/` request paths, accounts, usage, quota, migrations | `tests/integration`, `tests/e2e`, `make migration-check`, `scripts/lb-sandbox-check`, `python3 scripts/claude_cache_eval.py` after restart |
| spend caps | `tests/floor/test_api_keys_service.py`, `tests/integration/test_anthropic_cost_cap.py`, `tests/integration/test_seat_cursor_spend.py`, `scripts/lb-spend-cap-check` |
| `clients/route`, `clients/seat`, `config/coding-agents/**` | `config/coding-agents/verify-routing --source-only`, `tests/integration/test_route_*`, the install-time parity fixture `config/coding-agents/hooks/hook-dispatch-parity.py` |
| `clients/claude-lb-launch` | `CLAUDE_LB_DRY_RUN=1` round trip (AGENTS.md rule 2) |
| frontend | `make frontend-lint frontend-typecheck frontend-build` |
| menubar | `make menubar-build` |

## Done criteria

- [ ] `git ls-files tests/unit tests/agentlb scripts/tests clients/open-factory/tests clients/macos-menubar/Tests frontend/src/test` prints nothing
- [ ] `ls tests/floor/*.py | wc -l` prints `10` (nine tests plus `__init__.py`)
- [ ] Floor suite passes
- [ ] Gate parity prints `ok`
- [ ] `comm -13 /tmp/003-before.txt /tmp/003-after.txt` shows no new failure
- [ ] Frontend and Menubar commands exit 0
- [ ] `plans/README.md` row 003 updated

## STOP conditions

- A KEEP file fails after the move and passed before it.
- `msw` or anything in `frontend/src/test/` is imported by product code.
- An integration test imports from a file in the DELETE list other than `tests/unit/test_route_ladder.py` (`git grep -n "from tests\." -- tests/integration tests/e2e`).
- Any check outside this repo runs agent-lb unit tests by path (`rg -n "agent-lb.*tests/unit" /Volumes/StudioExt/repos/factory/checks /Volumes/StudioExt/repos/agent-rails/scripts`).
- The integration failure set grows and the new failure is not explained by a moved file.

## Maintenance notes

- New tests follow the test-audit authoring gate: integration or e2e first; a new unit test needs a written reason.
- The open-factory eval miner (`clients/open-factory/evals/of-work/build_tasks.py`) turns commits that ship tests into eval tasks. Commits after this plan still qualify when they ship integration tests; tell the evals lane that unit-only commits no longer exist.
- `openspec/specs/**` may cite deleted unit tests as evidence; those citations go stale and are not fixed here.
- Reviewer: check the `tests/floor/` list against the four kept floors, and that no floor test was thinned.

LOC removed (estimate): ~138,000 (128k Python unit, 8.8k frontend, 5.9k Swift, 1k other, minus ~5.9k kept).
