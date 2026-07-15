# Tasks

## 1. Retire client artifacts

- [x] 1.1 Delete `clients/ccdex`, `clients/ccdex-worker-mcp`,
      `config/coding-agents/ccdex-gpt-only.sh`,
      `config/coding-agents/codex-adapter.md`
- [x] 1.2 Delete `tests/unit/test_ccdex_worker_mcp.py` and
      `tests/integration/test_ccdex_worker_lifecycle.py`

## 2. Converge policy and installer

- [x] 2.1 Rewrite `ROUTING.md` and `claude-adapter.md` for the raw-harness mode
- [x] 2.2 Update `install-policy.py` (claude-only adapter, codex block removal,
      hook strip without re-add, keep Fable model pin)
- [x] 2.3 Update `scripts/install-claude-clients.sh` (cc-only install plus
      retired-artifact cleanup)
- [x] 2.4 Update `verify-routing` to assert retired artifacts are absent

## 3. Validate

- [x] 3.1 `uv run pytest tests/unit/test_install_claude_clients.py
    tests/unit/test_claude_lb_launch.py`
- [x] 3.2 `ruff check app clients`
- [x] 3.3 Converge the live machine (`install-policy.py`) and run
      `verify-routing` green
- [x] 3.4 OpenSpec strict validation passes
