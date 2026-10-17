# Plan 001: Dead modules, compatibility shims and one-off scripts are gone

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report; do not improvise. When done, update the status row for this plan
> in `plans/README.md`, unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat 63b62f18..origin/main -- app/core/clients/retry.py app/core/resilience/retry_budget.py app/modules/proxy/_support.py app/modules/proxy/_warmup.py scripts/check_proxy_architecture.py scripts/reproduce_responses_stream_load.py scripts/rewrite_alembic_revisions.py scripts/verify_v1_responses_openai_sdk.py scripts/stand_in_forced_swap.py scripts/verify_rollout_safe_bridge.py scripts/two-instance-exercise.sh scripts/two_instance_fedhelper.py clients/codex-routing-guard scripts/install-codex-routing-guard.sh`
> If any in-scope file changed since this plan was written, compare the
> "Current state" facts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: tech-debt
- **Planned at**: commit `63b62f18`, 2026-10-17

## Why this matters

The owner asked (2026-10-17) to remove as much legacy code as possible from
agent-lb. These files have no production caller: nothing imports them, no
launchd job runs them, no install script copies them, and no other repo calls
them. Their only callers are unit tests that exist to keep them alive. Deleting
them shrinks what every agent has to read and search.

## Current state

agent-lb is a Python 3.13 FastAPI service (`app/`), stdlib-only client scripts
(`clients/`), and operator scripts (`scripts/`). It runs live under launchd on
Studio from `~/.agent-lb/runtime/agent-lb`, synced from
`/Volumes/StudioExt/repos/agent-lb` at `origin/main`.

Files to delete, with the deadness proof gathered at `63b62f18`:

| File | Lines | Proof it is dead |
|------|------:|------------------|
| `app/core/clients/retry.py` | 31 | `git grep -n "RetryOptions\|calculate_backoff_delay"` hits only this file |
| `app/core/resilience/retry_budget.py` | 45 | only importer is `tests/unit/test_retry_budget.py` |
| `app/modules/proxy/_support.py` | 104 | re-export shim; `git grep -nE "modules\.proxy\._support"` hits only `openspec/` docs and `scripts/check_proxy_architecture.py` (which forbids importing it) |
| `app/modules/proxy/_warmup.py` | 35 | same as `_support.py` |
| `scripts/reproduce_responses_stream_load.py` | 131 | zero references in the repo, launchd, factory, agent-rails, `~/.agent-lb/bin` |
| `scripts/rewrite_alembic_revisions.py` | 179 | zero references; one-off from 2026-02 |
| `scripts/verify_v1_responses_openai_sdk.py` | 236 | zero references |
| `scripts/stand_in_forced_swap.py` | 36 | only `tests/unit/test_stand_in_widening.py` |
| `scripts/verify_rollout_safe_bridge.py` | 437 | only `tests/unit/test_verify_rollout_safe_bridge.py` |
| `scripts/two-instance-exercise.sh` | 212 | only openspec change docs |
| `scripts/two_instance_fedhelper.py` | 128 | only `scripts/two-instance-exercise.sh` |
| `clients/codex-routing-guard` | 257 | not installed (`~/.local/bin`, `~/.agent-lb/bin` lack it); its launchd label `com.aneyman.agent-lb-codex-routing-guard` is not in `~/Library/LaunchAgents`; only a prose mention on `clients/route` line 5 |
| `scripts/install-codex-routing-guard.sh` | 207 | installer for the dead guard above; only its unit test references it |

The ADR that created the shims (`DECISIONS.md`, ADR-0001) says "Compatibility
shims may remain while internal consumers migrate". The consumers have
migrated, so deleting them follows the ADR.

`scripts/check_proxy_architecture.py` (run by `make lint`) asserts the shims
exist and stay shim-only. Lines to change:

```python
# scripts/check_proxy_architecture.py:181-185
def _check_service_does_not_import_shims(module: ast.Module) -> None:
    forbidden = {"app.modules.proxy._support", "app.modules.proxy._warmup"}
    ...
# scripts/check_proxy_architecture.py:249-250 (inside main)
        _assert_shim_only(PROXY_DIR / "_support.py")
        _assert_shim_only(PROXY_DIR / "_warmup.py")
```

Keep `_check_service_does_not_import_shims` (it still stops a re-introduction
from being imported by `service.py`). Delete the two `_assert_shim_only(...)`
calls, and delete `_assert_shim_only` itself if nothing else calls it.

Unit tests that exist only for the deleted files, deleted in this plan:
`tests/unit/test_retry_budget.py`, `tests/unit/test_verify_rollout_safe_bridge.py`,
`tests/unit/test_codex_routing_guard.py`, `tests/unit/test_install_codex_routing_guard.py`.
`tests/unit/test_stand_in_widening.py` also tests `app/` code; leave it for
plan 003 but remove only the test functions that run `scripts/stand_in_forced_swap.py`
(find them with `grep -n "stand_in_forced_swap" tests/unit/test_stand_in_widening.py`).

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Worktree | `git -C /Volumes/StudioExt/repos/agent-lb worktree add /Volumes/StudioExt/repos/agent-lb-wt/cull-dead -b cull/dead-code origin/main` | exit 0 |
| Env | `sh scripts/worktree-setup.sh` (from the worktree) | exit 0 |
| Lint | `uv run --no-sync ruff check . && uv run --no-sync ruff format --check .` | exit 0 |
| Architecture | `python3 scripts/check_proxy_architecture.py` | prints `proxy architecture checks passed` |
| Types | `uv run --no-sync ty check` | exit 0, or the same errors as on `origin/main` |
| Import smoke | `uv run --no-sync python -c "import app.main; from app.main import create_app; create_app()"` | exit 0 |
| Integration | `uv run --no-sync pytest -q -n 4 --timeout=180 --timeout-method=thread tests/integration` | same failure set as the baseline |

Pytest and vitest are routed off Studio by a hook; if it tells you to, run the
same command through `seat-run --box auto --name <name> -- bash -lc "cd <worktree> && <command>"`.

## Scope

**In scope** (only these files change):
- delete every file in the table above
- delete `tests/unit/test_retry_budget.py`, `tests/unit/test_verify_rollout_safe_bridge.py`, `tests/unit/test_codex_routing_guard.py`, `tests/unit/test_install_codex_routing_guard.py`
- `tests/unit/test_stand_in_widening.py` (remove only the functions that use the deleted script)
- `scripts/check_proxy_architecture.py`
- `clients/route` line 5 only, to drop the name `clients/codex-routing-guard` from the header text

**Out of scope**:
- `scripts/cut-client-release.sh`: it has no in-repo caller, but `~/.agent-lb/client-releases/main-10aa981e` was made this week and `~/.local/bin/claude-lb-launch` points into it. It is live. Do not delete it.
- `scripts/sim-relay.py` (launchd `com.agent-lb.sim-relay`), `scripts/lb-reboot-drill` (agent-rails proof spec), `scripts/install-spend-cap-check.sh` (factory `checks/harden/lb-spend-cap`): live.
- `openspec/` documents that mention the shims: leave them; plan 008 handles openspec.

## Git workflow

- Branch `cull/dead-code` in the worktree above; one commit.
- Message style (from `git log`): imperative, plain, e.g. `Delete dead retry modules, proxy shims and one-off scripts`. Body ends with one `check:` line (the command and its result) and a `Seat: <seat> (<model>)` trailer.
- Land per repo rule: `git push origin HEAD:main` (fast-forward only; on rejection, rebase once on `origin/main` and retry).

## Steps

### Step 1: Record the baseline

Run the Integration command on the untouched worktree and save the failing test
ids: `... tests/integration 2>&1 | grep -E "^(FAILED|ERROR)" | sort > /tmp/001-before.txt`.

**Verify**: `test -f /tmp/001-before.txt` → exit 0.

### Step 2: Re-prove deadness

For each file in the table, run `git grep -nF "<basename without extension>" -- . ':!CHANGELOG.md' ':!openspec'`
and `rg -lF "<basename>" ~/Library/LaunchAgents ~/.agent-lb/bin /Volumes/StudioExt/repos/factory /Volumes/StudioExt/repos/agent-rails`.

**Verify**: every hit is the file itself, a test listed for deletion above, `scripts/check_proxy_architecture.py`, or `clients/route` line 5. Any other hit is a STOP.

### Step 3: Delete the files and their tests

`git rm` every file in scope. Edit `tests/unit/test_stand_in_widening.py` to drop
only the functions that call the deleted script; drop now-unused imports.

**Verify**: `uv run --no-sync ruff check .` → exit 0.

### Step 4: Update the architecture ratchet

Remove the two `_assert_shim_only(...)` calls in `main()` and the
`_assert_shim_only` function if unused. Keep `_check_service_does_not_import_shims`.

**Verify**: `python3 scripts/check_proxy_architecture.py` → `proxy architecture checks passed`.

### Step 5: Smoke and compare

Run the Import smoke and Integration commands. Save failures to `/tmp/001-after.txt`.

**Verify**: `comm -13 /tmp/001-before.txt /tmp/001-after.txt` → empty (no new failures).

### Step 6: Land and roll out

Commit, push to `origin/main`. Then `git -C /Volumes/StudioExt/repos/agent-lb pull --ff-only`,
`~/.agent-lb/bin/sync-runtime.sh`, and `~/.agent-lb/bin/lb-restart --reason "plan 001: delete dead modules"`.

**Verify**: `curl -fsS http://127.0.0.1:2455/health/ready` → HTTP 200.

## Test plan

No new tests. Removed coverage is replaced by the existing checks: the import
smoke, `make lint` (ruff plus the architecture ratchet), and the integration suite.

## Done criteria

- [ ] `git ls-files app/core/clients/retry.py app/core/resilience/retry_budget.py app/modules/proxy/_support.py app/modules/proxy/_warmup.py clients/codex-routing-guard` prints nothing
- [ ] `python3 scripts/check_proxy_architecture.py` prints `proxy architecture checks passed`
- [ ] `uv run --no-sync ruff check .` exits 0
- [ ] `comm -13 /tmp/001-before.txt /tmp/001-after.txt` is empty
- [ ] `curl -fsS http://127.0.0.1:2455/health/ready` returns 200 after `lb-restart`
- [ ] `plans/README.md` row 001 updated

## STOP conditions

- Step 2 finds a caller outside the list (a launchd plist, an installed copy, another repo).
- `~/.agent-lb/runtime/agent-lb` imports `app.modules.proxy._support` or `_warmup` (`rg -n "proxy\._support|proxy\._warmup" ~/.agent-lb/runtime/agent-lb/app`).
- `lb-restart` reports a failed health gate or rolls back.

## Maintenance notes

- If a later change wants retry backoff, use the retry logic that lives in the
  module that needs it; do not revive `app/core/clients/retry.py`.
- Reviewer: confirm no `openspec/specs/**` requirement names a deleted script as a required tool.

LOC removed (estimate): ~2,040 source lines plus ~1,200 test lines.
