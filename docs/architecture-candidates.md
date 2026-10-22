# Architecture deepening candidates

Survey of 2026-10-18 (slice W1-arch-agent-lb). Read-only on source. Implementation is a later slice per candidate.

Method: commits in the last 60 days that touch a cluster (868 commits in the window; commits over 40 files excluded as mechanical), times the distinct modules those commits also touched (tests, openspec and Markdown excluded). A module is `app/<area>/<pkg>`, `clients/<bin>` or `config/coding-agents/<path>`.

| Rank | Candidate | Commits | Modules touched | Score |
|---|---|---|---|---|
| 1 | Routing policy reader | 177 | 77 | 13,629 |
| 2 | Seat and route run record | 91 | 56 | 5,096 |
| 3 | Anthropic request pipeline | 82 | 47 | 3,854 |
| 4 | Policy install and verify | 112 | 26 | 2,912 |
| 5 | Schema and migrations | 38 | 42 | 1,596 |

Deletion test: imagine deleting the module. If its complexity reappears across many callers, the module is deep and earns its place. If it barely moves, the module is shallow.

## 1. Routing policy reader

Files: `config/coding-agents/routing-table.json`, `app/core/routing_table.py`, `clients/route` (`resolve_alias`, `readmitted_patterns`, `resolve_claude_family`), `clients/seat` (`resolve_model`), `clients/claude-lb-launch`, `clients/muse-lb-launch`, `config/coding-agents/hooks/seat-guard.py`, `config/coding-agents/hooks/workflow-seat-guard.py`, `app/modules/proxy/meta_service.py`, `app/modules/proxy/request_policy.py`, `app/core/usage/pricing.py`, `app/core/anthropic/pricing.py`.

Problem: nine readers find the table themselves (`ROUTE_TABLE`, then the managed copy, then a repo or `~/.agents` fallback, in orders that differ), and seven functions resolve an alias. A rule change to retired or readmitted models has to land in each one.

Interface: one stdlib-only module `clients/routing_policy.py` that both `app` and the client scripts import:
`load(path=None) -> Policy`; `Policy.resolve(alias) -> Resolved(model, vendor, effort, retired, readmitted)`; `Policy.aliases() -> Mapping[str, str]`; `Policy.source -> Path`. `app/core/routing_table.py` becomes the import of it.

Deletion test today: deleting `app/core/routing_table.py` moves about 20 lines into two callers, so it is shallow. Deleting the proposed module would scatter path lookup and alias rules back into nine files, so it is deep.

Slice: add `routing_policy.py` and move the readers in `clients/route`, `clients/seat` and both hooks onto it. Behavior is unchanged. Check: `uv run pytest -q tests/floor/test_policy_core.py tests/floor/test_seat_guard_quota_swap.py`

## 2. Seat and route run record

Files: `clients/seat`, `clients/route`, `clients/route_quota.py`, `clients/route_storage.py`, `clients/route_cache.py`, `clients/route_anthropic_models.py`, `clients/route_build_outcomes.py`, `clients/route_class_candidates.py`, `clients/route_dispatch.py`, `clients/route_ladder_walk.py`, `clients/route_learn.py`, `clients/route_learning_text.py`, `clients/route_pace_state.py`, `clients/route_pick.py`, `clients/route_probe_model.py`, `clients/route_reserve_state.py`, `clients/route_resolve_one.py`, `clients/route_seat.py`, `clients/route_seat_states.py`, `clients/route_versioned_rules.py`, `clients/route_workflow_args.py`.

Problem: `seat` runs `route` as a subprocess (`ROUTE_BIN`), and the two binaries each define `append_ledger`, `ledger_path`, `state_path`, `finish_text`, `command_release`, `same_model` and `cursor_models`, with drifting shapes. For example, the route ledger row adds `ts` and `cwd` and the seat one does not. Both files are far over the 20 KB cap.

Interface: `clients/run_record.py` with `RunLedger(path).append(event: dict) -> None` (one row shape: `ts`, `cwd`, `kind`, fields), `release(lease_id) -> Released`, `finish_text(result) -> str` and `same_model(a, b) -> bool`. `route_storage.atomic_write` moves into it.

Deletion test: the duplicated pairs show the logic already reappears in two callers. One module removes seven duplicate functions and pins one row format that ledger readers can trust.

Slice: extract `run_record.py`, point both binaries at it, and delete the duplicates. Check: `make test-floor`

## 3. Anthropic request pipeline

Files: `app/modules/proxy/api.py` (173 KB, 15 routes, 170 top-level defs), `app/modules/proxy/anthropic_service.py` (131 KB), `app/modules/proxy/load_balancer.py` (124 KB).

Problem: the route layer reaches into service internals (`api.py` imports `_anthropic_request_session_id` and `_is_fable_model`) and wraps the service's `claim_session_route` in its own affinity-key logic (`claim_anthropic_session_route`). Request policy is split across both files, so most Anthropic fixes touch both.

Interface: `AnthropicGateway` with `messages(request, principal) -> StreamingResponse | JSONResponse` and `count_tokens(request, principal) -> JSONResponse`. Account selection, session affinity, retry and stall handling sit behind it. `api.py` keeps only FastAPI wiring.

Deletion test: deleting `anthropic_service.py` today would push stream retry, stall detection and quota eligibility into `api.py`, so it is deep inside. Its boundary leaks, though, and private names cross it. The slice closes the boundary rather than adding a layer.

Slice: move the session-route claim and the two private helpers behind the gateway, and stop `api.py` importing `_`-prefixed names from `anthropic_service`. Check: `uv run python scripts/check_proxy_architecture.py && uv run pytest -q tests/floor/test_anthropic_cost_cap.py tests/floor/test_anthropic_paid_fallback.py`

## 4. Policy install and verify

Files: `config/coding-agents/install-policy.py` (91 KB, 62 functions), `config/coding-agents/verify-routing` (37 KB), `config/coding-agents/hooks/hook-dispatch.py` (55 KB), `config/coding-agents/hooks/hook-dispatch-parity.py` (66 KB).

Problem: install, check, preview and uninstall are flag branches inside one `main()`. Hook classification (`is_owned_hook`, `is_seat_guard_hook`, `is_dispatch_hook`, `foldable_groups` and others) lives in the installer, while `hook-dispatch.py` holds the runtime view of the same hook set. The parity fixture exists because the two can disagree.

Interface: `config/coding-agents/policy_plan.py` with `plan(source, home) -> Plan` (a list of `FileChange` and `SettingsChange`), `Plan.apply(checkpoint=True)`, `Plan.check() -> list[Problem]` and `hook_registry() -> list[HookSpec]`, which both the installer and the dispatcher read. `install-policy.py` becomes argument parsing plus `plan(...).apply()` or `.check()`.

Deletion test: deleting the installer would scatter writes to `~/.claude` and `~/.codex` across scripts, so it is deep. The hook registry is duplicated, though, and deleting either copy loses nothing the other cannot supply.

Slice: extract `hook_registry()` and point both the installer and `hook-dispatch.py` at it, with no change to what gets installed. Check: `python3 config/coding-agents/install-policy.py --check && uv run pytest -q tests/floor/test_hook_dispatcher.py`

## 5. Schema and migrations

Files: `app/db/models.py`, `app/db/migrate.py`, `app/db/schema_drift.py`, `app/db/alembic/versions/` (152 revisions).

Problem: `models.py` is touched in 25 of 38 schema commits, and each feature that adds a column also edits its module's repository, so the 42 modules touched mostly come from model edits. Revisions dated 2026-09 still run on every fresh database.

Interface: per-module table declarations (`app/modules/<m>/tables.py`) registered on one `Base`. `migrate.upgrade(engine) -> Report` and `schema_drift.inspect(engine) -> list[Drift]` stay as the only entry points. A squashed baseline revision replaces revisions older than the oldest deployed database.

Deletion test: deleting `migrate.py` would push lock, timeout and drift handling to every startup path, so it is deep. `models.py` as one file is shallow: it is a list, and splitting it by owner moves no logic.

Slice: move the account, pool and team tables into their modules' `tables.py` with no schema change. Check: `make migration-check`

## Not ranked

`app/modules/accounts` scored 1,224 and was split on 2026-10-17. `frontend/src` changes stay inside the frontend.
