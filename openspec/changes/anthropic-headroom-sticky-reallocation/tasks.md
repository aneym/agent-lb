# Tasks

- [x] Add `anthropic_sticky_headroom_reallocation_enabled` setting (default true) in `app/core/config/settings.py`
- [x] Thread `headroom_reallocate` through `LoadBalancer.select_account` → `_select_with_stickiness`; under budget pressure with no selectable burn-first candidate, rebind to the best budget-safe eligible account (reuse the existing anti-thrash keep-pinned guard); persist the new mapping
- [x] Pass the flag from `anthropic_service._select_account`, gated on the new setting (Anthropic only)
- [x] Unit tests: threshold-crossing rebind persisted; pool-exhausted keeps pin; no flap-back after old window reset; disabled flag preserves old behavior; OpenAI paths unaffected
- [x] Regression test: in-request 429 failover leaves the durable mapping on the serving account (fix if the assertion fails)
- [x] Launcher: stable cwd-derived session id for headless invocations without `CLAUDE_LB_SESSION_ID`; interactive unchanged; explicit id wins
- [x] Launcher validation: `python -m py_compile` + `CLAUDE_LB_DRY_RUN=1` round-trip showing the stable id
- [x] `uv run ruff check app clients tests` and targeted pytest suites pass
