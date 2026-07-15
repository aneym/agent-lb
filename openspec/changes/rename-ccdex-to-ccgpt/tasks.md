# Tasks

## 1. Server

- [x] 1.1 Rename `/v1/ccdex/messages` and `/v1/ccdex/messages/count_tokens`
      to `/v1/ccgpt/messages` and `/v1/ccgpt/messages/count_tokens` in
      `app/modules/proxy/api.py`
- [x] 1.2 Rename `CCDEX_MODEL_ALIASES`, `_ccdex_messages_response`, and the
      route handler function names to `CCGPT_*`/`_ccgpt_*`
- [x] 1.3 Rename `CCDEX_*` constants in
      `app/modules/proxy/claude_codex_bridge.py` to `CCGPT_*`

## 2. Client

- [x] 2.1 Rename the dormant codex-mode branch's internal identifiers and
      `/v1/ccdex/messages` path string in `clients/claude-lb-launch` to
      `ccgpt`; keep `CLAUDE_LB_CODEX_MODE` unchanged

## 3. Tests and Specs

- [x] 3.1 Rename `tests/integration/test_ccdex_proxy.py` to
      `test_ccgpt_proxy.py` and update its contents
- [x] 3.2 Update `tests/unit/test_claude_lb_launch.py`,
      `tests/unit/test_anthropic_proxy_api.py`,
      `tests/unit/test_claude_codex_bridge.py`,
      `tests/unit/test_startup_benchmark.py`
- [x] 3.3 Update the `claude-harness-codex`, `account-routing`,
      `runtime-portability`, and `startup-performance` main specs for the
      route/profile naming only, leaving the already-retired launcher and
      worker-transport requirements untouched

## 4. Validation

- [x] 4.1 `ruff check app clients` clean
- [x] 4.2 `pytest tests/integration/test_ccgpt_proxy.py
    tests/unit/test_claude_lb_launch.py
    tests/unit/test_install_claude_clients.py` pass
- [x] 4.3 OpenSpec strict validation passes
- [x] 4.4 Live service restarted; a real `/v1/ccgpt/messages` round-trip
      returns 200 and `/v1/ccdex/messages` returns 404
