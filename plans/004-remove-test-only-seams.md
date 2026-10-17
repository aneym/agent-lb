# Plan 004: Production code no longer carries hooks that only deleted unit tests used

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report; do not improvise. When done, update the status row for this plan
> in `plans/README.md`, unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat 63b62f18..origin/main -- app/core/middleware/firewall_cache.py app/modules/usage/identity_mismatch.py app/modules/proxy/service.py scripts/check_proxy_architecture.py`
> If any in-scope file changed since this plan was written (other than by
> plans 001-003), compare the "Current state" excerpts against the live code;
> on a mismatch, treat it as a STOP condition.

## Status

- **Priority**: P2
- **Effort**: S
- **Risk**: LOW
- **Depends on**: plans/003 (the unit tests that call these hooks must be gone first)
- **Category**: tech-debt
- **Planned at**: commit `63b62f18`, 2026-10-17

## Why this matters

Unit tests made production modules grow exports that no production caller
uses: reset functions and private constants re-exported from the proxy
façade so tests could reach them. After plan 003 deletes the unit tests,
these exports are dead code that still widens each module's interface.

## Current state

Test-only reset hooks (each defined once, called only from tests at `63b62f18`):

```python
# app/core/middleware/firewall_cache.py:76
def reset_firewall_ip_cache_for_testing() -> None:
# app/modules/usage/identity_mismatch.py:80-82
def reset_for_tests() -> None:
    with _lock:
        _state.clear()
```

At `63b62f18` their callers were `tests/unit/test_firewall_cache_ttl.py`,
`tests/unit/test_hot_path_caches.py` and `tests/unit/test_no_accounts_diagnostics.py`,
all deleted by plan 003.

The proxy façade ratchet (`scripts/check_proxy_architecture.py`, lines 44-61)
requires `app/modules/proxy/service.py` to keep these names importable. The
first group has no caller in `app/`, `tests/integration/` or `tests/e2e/`;
only deleted unit tests used them:

```
_API_KEY_RESERVATION_HEARTBEAT_SECONDS  _HARD_HTTP_BRIDGE_AFFINITY_KINDS
_REQUEST_TRANSPORT_WEBSOCKET            _WEBSOCKET_FULL_REPLAY_WAIT_MIN_ITEMS
_WEBSOCKET_FULL_REPLAY_WAIT_POLL_SECONDS
pop_compact_timeout_overrides  push_compact_timeout_overrides
pop_transcribe_timeout_overrides  push_transcribe_timeout_overrides
```

Keep in the set (integration tests monkeypatch them through the façade):
`ProxyService`, `CodexControlResponse`, `core_codex_control_request`,
`core_compact_responses`, `core_create_file`, `core_finalize_file`,
`core_transcribe_audio`.

Not in scope: `ROUTE_FIXTURE_DIR` in `clients/route` (lines 44 and 502). It is a
test seam, but integration tests use it (`tests/integration/route_http_fixtures.py`,
`test_muse_code_bootstrap.py`, `test_muse_seat_cli.py`, `test_route_pick_leases.py`,
`test_route_reservations.py`), so it stays.

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Worktree | `git -C /Volumes/StudioExt/repos/agent-lb worktree add /Volumes/StudioExt/repos/agent-lb-wt/cull-seams -b cull/test-seams origin/main` | exit 0 |
| Env | `sh scripts/worktree-setup.sh` | exit 0 |
| Callers | `git grep -n "<name>" -- app clients scripts tests` | only the definition (and, for façade names, `service.py` itself) |
| Architecture | `python3 scripts/check_proxy_architecture.py` | `proxy architecture checks passed` |
| Lint | `uv run --no-sync ruff check . && uv run --no-sync ruff format --check .` | exit 0 |
| Types | `uv run --no-sync ty check` | no new errors versus `origin/main` |
| Integration | `uv run --no-sync pytest -q -n 4 --timeout=180 --timeout-method=thread tests/integration tests/e2e tests/floor` | same failure set as baseline |

## Scope

**In scope**: `app/core/middleware/firewall_cache.py`, `app/modules/usage/identity_mismatch.py`,
`app/modules/proxy/service.py` (import lines only), `scripts/check_proxy_architecture.py`.

**Out of scope**: `clients/route` and its `ROUTE_FIXTURE_DIR`; behavior of any
function; the façade names in the keep group.

## Git workflow

- Branch `cull/test-seams`; one commit, e.g. `Drop reset hooks and façade names only unit tests used`; `check:` line and `Seat:` trailer.
- Land: `git push origin HEAD:main`; then `git -C /Volumes/StudioExt/repos/agent-lb pull --ff-only`, `~/.agent-lb/bin/sync-runtime.sh`, `~/.agent-lb/bin/lb-restart --reason "plan 004: drop test-only seams"`.

## Steps

### Step 1: Baseline

Run Integration; save failures to `/tmp/004-before.txt`.

**Verify**: file exists.

### Step 2: Remove the reset hooks

For each of the two functions run Callers. If only the definition remains,
delete the function.

**Verify**: `git grep -n "reset_for_tests\|reset_firewall_ip_cache_for_testing" -- app` prints nothing; Lint exits 0.

### Step 3: Narrow the façade

For each name in the first group: run Callers. Remove it from
`REQUIRED_SERVICE_FACADE_NAMES`. Then, in `service.py`, delete its import line
only if `service.py` does not use the name anywhere else
(`grep -c "<name>" app/modules/proxy/service.py` prints `1`).

**Verify**: Architecture passes; Types shows no new errors.

### Step 4: Compare, land, restart

Run Integration; save `/tmp/004-after.txt`.

**Verify**: `comm -13 /tmp/004-before.txt /tmp/004-after.txt` is empty; after restart `curl -fsS http://127.0.0.1:2455/health/ready` returns 200.

## Test plan

No new tests. Integration, e2e and floor suites carry the behavior.

## Done criteria

- [ ] `git grep -n "reset_for_tests\|reset_firewall_ip_cache_for_testing" -- app` prints nothing
- [ ] `grep -c "_WEBSOCKET_FULL_REPLAY_WAIT_POLL_SECONDS\|push_compact_timeout_overrides" scripts/check_proxy_architecture.py` prints `0`
- [ ] Architecture passes; no new integration failures
- [ ] `plans/README.md` row 004 updated

## STOP conditions

- A name in the first group has a caller in `app/`, `clients/`, `tests/integration/`, `tests/e2e/` or `tests/floor/`.
- Deleting a reset function changes module state handling elsewhere (the function body is more than clearing a cache).

## Maintenance notes

- A future integration test that needs a cache reset should go through the real interface (a fresh app instance from `tests/conftest.py`), not a new `_for_tests` export.

LOC removed (estimate): ~40.
