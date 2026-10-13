# Muse preparation check, 2026-10-13

This is an offline preparation receipt. Studio execution remains blocked.

The host reports Linux on ax42. The supplied Studio worktree and Muse binary
paths are absent. SSH to Studio fails with `socket: Operation not permitted`.
No credentials, Muse configuration, lanes or live agent packages were read.
No runtime was restarted, tool installed on a host, tab created or commit pushed.

## Passing checks

`HOME="$PWD/tmp/check-home" .venv/bin/python -m pytest tests/integration/test_muse_seat_cli.py tests/unit/test_seat_run_usage.py tests/unit/test_route_cli.py tests/unit/test_route_ladder_contract.py tests/test_provider_cli_guard.py -q`

```text
117 passed in 68.74s (0:01:08)
```

`HOME="$PWD/tmp/check-home" env -u BOX_EXEC_HOST .venv/bin/python -m pytest tmp/open-factory-muse/engine/checks/seat-run/test_muse.py tmp/open-factory-muse/engine/checks/seat-run/test_lane_ship.py -q`

```text
12 passed in 1.82s
```

`HOME="$PWD/tmp/check-home" ROUTE_TABLE="$PWD/config/coding-agents/routing-table.json" python3 clients/route resolve muse-latest`

```text
muse-wonder-1.0
```

The Muse integration check invokes `route pools --json` against the HTTP fixture
boundary, confirms the Muse pool and unknown remaining percentage, and checks
real state and ledger files. This is not a live endpoint receipt.

Python byte compilation and `git diff --check` pass for both repositories.
`openspec validate --specs` reports `40 passed, 0 failed`.
`openspec validate add-muse-code-seat --strict` reports the change is valid.
Changed Muse adapter, pool and test files pass targeted Ruff checks.

## Failed or unverified checks

`ruff check app clients` fails on the pre-existing E501 in `app/audit_tabs.py:122`.
The same line exists at starting commit b8af7d2a. No unrelated lint edit was made.

`tests/integration/test_seat_cli.py` reports `1 failed, 1 passed`. Its Cursor
stdin test expects the original prompt, but the existing launcher appends its
finish contract. A temporary copy using b8af7d2a's launcher reproduces that same
failure. This is recorded rather than weakening the check.

The HTTP pool test times out in fixture setup at
`test_pools_router_declares_dashboard_auth_dependency`, before endpoint execution.
It was stopped once and rerun with a five-second pytest timeout to capture the
stall. No database or fixture behavior was changed.

Remaining checks require Studio: the native CLI, authenticated scratch run,
real JSONL/usage, live pool and LB request log, safe restart and endpoint health,
Raise package equivalence, tab status answer and live MCP inventory. No tab ID
is available. Token counts and subscription quota remain unknown.

The open-factory checkout is isolated under this workspace's `tmp/`, based on
local origin/main f7b028462. This deviates from the absent Studio path. Transfer
its local commit or bundle to Studio; never push under this brief.
