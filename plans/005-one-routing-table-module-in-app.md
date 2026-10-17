# Plan 005: One routing-table module owns finding and reading the table inside `app/`

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report; do not improvise. When done, update the status row for this plan
> in `plans/README.md`, unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat 63b62f18..origin/main -- app/modules/routing_policy/service.py app/modules/pools/plan.py app/modules/pools/cli_seats.py app/modules/pools/service.py app/modules/proxy/meta_service.py app/modules/proxy/stand_in.py`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P2
- **Effort**: S
- **Risk**: MED (money path: seat routing and pool display read this table)
- **Depends on**: plans/003 (unit tests that monkeypatch the old private helpers are gone)
- **Category**: tech-debt (architecture: deepening)
- **Planned at**: commit `63b62f18`, 2026-10-17

## Why this matters

The coding-agent routing table (`routing-table.json`) is the policy agent-lb
serves. Inside `app/`, five modules each locate it with their own copy of the
same three-step lookup (env `ROUTE_TABLE`, then
`~/.agent-lb/managed/coding-agents/routing-table.json`, then the repo copy),
and each parses it with its own error handling. One of them,
`app/modules/proxy/stand_in.py`, reaches across a seam to import a private
helper (`_live_paths`) from `app/modules/routing_policy/service.py`. A change to
where the table lives (the hot spot: `routing-table.json` and
`install-policy.py` are the most-changed files in the last 300 commits) has to
be made five times. One deep module gives locality: one place to change, one
interface every caller uses.

## Current state

The five copies at `63b62f18`:

```python
# app/modules/routing_policy/service.py:17-23 (inside _live_paths, which also finds decider.json)
    managed = Path.home() / ".agent-lb/managed/coding-agents/routing-table.json"
    table = (
        Path(os.environ["ROUTE_TABLE"]).expanduser()
        if os.environ.get("ROUTE_TABLE")
        else (managed if managed.is_file() else _REPO_ROOT / "config/coding-agents/routing-table.json")
    )

# app/modules/pools/plan.py:16-21
def installed_table_path() -> Path:
    managed = Path.home() / ".agent-lb/managed/coding-agents/routing-table.json"
    return (Path(os.environ["ROUTE_TABLE"]).expanduser() if os.environ.get("ROUTE_TABLE")
            else (managed if managed.is_file() else _CONFIG / "routing-table.json"))

# app/modules/pools/cli_seats.py:589-601
def seat_pricing_config() -> dict:
    managed = ...same three-step lookup...
    document = _read_state(path) or {}
    return document.get("cli_pools") or {}

# app/modules/pools/service.py:353-366 (inline inside a method)
        managed = ...same three-step lookup...
        try:
            document = json.loads(table.read_text(encoding="utf-8"))
            config = document.get("cli_pools", {}) if isinstance(document, dict) else {}
        except (OSError, UnicodeError, json.JSONDecodeError):
            config = {}

# app/modules/proxy/meta_service.py:75-81
def _route_table_path() -> Path:
    ...same three-step lookup...
```

And the seam leak:

```python
# app/modules/proxy/stand_in.py:13, 28
from app.modules.routing_policy.service import _live_paths
        table = json.loads(_live_paths()[0].read_text())
```

Callers of the helpers: `app/modules/pools/plan.py:26` (`installed_table_path()`),
`app/modules/pools/cli_seats.py:263, 631` (`seat_pricing_config()`),
`app/modules/proxy/meta_service.py:88` (`_route_table_path()`),
`app/modules/routing_policy/service.py:120` and `app/modules/proxy/stand_in.py:28` (`_live_paths()`).

Out-of-`app/` copies exist too (`clients/route:152`, `clients/seat:763`,
`clients/claude-lb-launch:129`, `config/coding-agents/hooks/seat-guard.py:323`,
`config/coding-agents/hooks/workflow-seat-guard.py:340`). Those are stdlib-only
scripts installed as single files; they cannot import `app/`. They are out of scope.

Repo conventions: modules under `app/core/` are domain-free helpers imported by
`app/modules/*`; type hints everywhere; `from __future__ import annotations`
at the top (see `app/core/eval_guard.py`). No comments or docstrings (house rule).

## Target interface

New file `app/core/routing_table.py`:

```python
def routing_table_path() -> Path: ...
def read_routing_table() -> dict[str, Any]: ...
```

- `routing_table_path()`: `ROUTE_TABLE` env (expanded) if set; else the managed copy if it is a file; else `<repo>/config/coding-agents/routing-table.json`, where `<repo>` is `Path(__file__).resolve().parents[2]`.
- `read_routing_table()`: parses `routing_table_path()`; returns `{}` on `OSError`, `UnicodeError`, `json.JSONDecodeError`, or a non-dict document.

Callers after the change:

| Caller | Uses |
|--------|------|
| `pools/plan.py` `load_plan_inputs` | `routing_table_path()` and its own strict `json.loads` (keeps raising on a broken table, as today); `installed_table_path` is deleted, its callers use `routing_table_path` |
| `pools/cli_seats.py` `seat_pricing_config` | `read_routing_table().get("cli_pools") or {}` |
| `pools/service.py` inline block | `config = read_routing_table().get("cli_pools", {})` |
| `proxy/meta_service.py` `resolve_model` | `read_routing_table()`; `_route_table_path` deleted |
| `routing_policy/service.py` `_live_paths` | `routing_table_path()` for the table half; keeps the decider lookup |
| `proxy/stand_in.py` | `read_routing_table()`; no import from `routing_policy.service` |

Keep each caller's exact error behavior: today `meta_service.resolve_model`
catches `KeyError`/`TypeError`/`AttributeError` too; keep those around the
dict access. `stand_in.py` today lets a parse error raise; check its call site
(`sed -n 20,40p app/modules/proxy/stand_in.py`) and keep the same outcome
(if it catches around the call, `{}` is equivalent; if it does not, use
`json.loads(routing_table_path().read_text())` there instead).

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Worktree | `git -C /Volumes/StudioExt/repos/agent-lb worktree add /Volumes/StudioExt/repos/agent-lb-wt/routing-table-module -b arch/routing-table-module origin/main` | exit 0 |
| Env | `sh scripts/worktree-setup.sh` | exit 0 |
| Copies left | `git grep -n "managed/coding-agents/routing-table.json" -- app` | exactly one hit, in `app/core/routing_table.py` |
| Leak gone | `git grep -n "_live_paths" -- app ':!app/modules/routing_policy'` | no output |
| Lint | `uv run --no-sync ruff check . && uv run --no-sync ruff format --check .` | exit 0 |
| Types | `uv run --no-sync ty check` | no new errors versus `origin/main` |
| Integration | `uv run --no-sync pytest -q -n 4 --timeout=180 --timeout-method=thread tests/integration tests/e2e tests/floor` | same failure set as baseline |
| Live smoke | `curl -fsS http://127.0.0.1:2455/api/pools -o /dev/null -w "%{http_code}\n"` after restart | `200` (if it needs a dashboard session, use `curl -fsS http://127.0.0.1:2455/health/ready`) |

## Scope

**In scope**: create `app/core/routing_table.py`; edit the six files listed in the drift check.

**Out of scope**: everything under `clients/`, `config/`, `scripts/`; the decider
lookup in `_live_paths`; `model-costs.json` lookup in `pools/plan.py`.

## Git workflow

- Branch `arch/routing-table-module`; one commit, e.g. `One routing-table module for app callers`; `check:` line and `Seat:` trailer.
- Land: `git push origin HEAD:main`; then `git -C /Volumes/StudioExt/repos/agent-lb pull --ff-only`, `~/.agent-lb/bin/sync-runtime.sh`, `~/.agent-lb/bin/lb-restart --reason "plan 005: routing-table module"`.

## Steps

### Step 1: Baseline

Run Integration; save failures to `/tmp/005-before.txt`. Run
`ROUTE_TABLE=/nonexistent uv run --no-sync python -c "from app.modules.proxy.meta_service import resolve_model; print(resolve_model('muse-latest'))"`
and record the output (the default model when the table is missing).

**Verify**: both recorded.

### Step 2: Add the module

Write `app/core/routing_table.py` with the two functions.

**Verify**: `uv run --no-sync python -c "from app.core.routing_table import routing_table_path, read_routing_table; print(routing_table_path()); print(sorted(read_routing_table())[:3])"` prints a path and three top-level keys.

### Step 3: Switch the callers

Edit each caller per the table. Delete `installed_table_path`, `_route_table_path`,
and the inline lookup; repoint their callers.

**Verify**: Copies left and Leak gone give their expected results; Lint exits 0.

### Step 4: Same behavior

Re-run the Step 1 `resolve_model` command.

**Verify**: same output as Step 1. Run Integration and save `/tmp/005-after.txt`; `comm -13 /tmp/005-before.txt /tmp/005-after.txt` is empty.

### Step 5: Land and restart

**Verify**: Live smoke returns 200; `python3 config/coding-agents/verify-routing` exits 0.

## Test plan

No new unit tests (owner's direction). The interface is the test surface:
the integration suites that exercise pools, seats and routing policy
(`tests/integration/test_*pool*`, `test_route_*`, `test_muse_*`) cover the
callers through HTTP and subprocess boundaries.

## Done criteria

- [ ] Copies left prints exactly one hit
- [ ] Leak gone prints nothing
- [ ] No new integration failures
- [ ] Live smoke 200 after restart
- [ ] `plans/README.md` row 005 updated

## STOP conditions

- A caller's error behavior cannot be kept with the two functions (for example it needs to distinguish "missing" from "broken").
- `lb-restart` rolls back.
- Any integration test monkeypatches one of the deleted helpers (`git grep -n "installed_table_path\|_route_table_path\|_live_paths" -- tests`); repoint the patch to `app.core.routing_table.routing_table_path` and note it in the commit.

## Maintenance notes

- Future readers of the routing table inside `app/` call `read_routing_table()`; do not add a sixth lookup.
- A later plan may give the stdlib scripts (`clients/route`, `clients/seat`, hooks) one shared lookup; it needs an installed shared module and was judged speculative on 2026-10-17.

LOC removed (estimate): ~45 net (five lookups collapse into one module).
