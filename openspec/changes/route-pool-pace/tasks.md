# Tasks
- [x] Implement `pace_state` with daily-burn projections and weekly/monthly pace fallback.
- [x] Implement local history loading, sampling, pruning and nonblocking write failure handling.
- [x] Record successful pool fetches from pools, pick, menu and doctor --write.
- [x] Add JSON pace objects and a text pace column without changing routing eligibility.
- [x] Run `uv run pytest tests/unit/test_route_pace.py tests/unit/test_route_cli.py -q` (72 passed).
- [x] Validate the OpenSpec change with `npx @fission-ai/openspec validate route-pool-pace --strict` (reported valid by the team lead).
