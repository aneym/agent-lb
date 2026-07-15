# Rename CCDEX to CCGPT across routes and identifiers

## Why

The owner decided the Messages-route GPT compatibility profile is "GPT
through Claude Code," not the retired Codex-host mode. Continuing to name it
`ccdex` invites confusion with the already-retired `ccdex` CLI/MCP-worker
stack (`retire-codex-dispatch-clients`). This change is a pure naming cut:
`/v1/ccdex/messages` becomes `/v1/ccgpt/messages`, and every server-side and
client-side identifier that names the live bridge/alias route follows. No
behavior change.

## What Changes

- Server identifiers in `app/modules/proxy/api.py`: `CCDEX_MODEL_ALIASES` ->
  `CCGPT_MODEL_ALIASES`, `_ccdex_messages_response` -> `_ccgpt_messages_response`,
  and the dedicated route handlers/functions follow the same rename.
- `app/modules/proxy/claude_codex_bridge.py`'s `CCDEX_*` constants become
  `CCGPT_*` (module filename unchanged).
- `clients/claude-lb-launch`'s dormant codex-mode branch: internal
  identifiers and its `/v1/ccdex/messages` path string follow the same
  rename; the `CLAUDE_LB_CODEX_MODE` env var name is unchanged (external
  contract exercised by `tests/unit/test_claude_lb_launch.py`).
- Retired-artifact cleanup strings (the already-deleted `ccdex`/
  `ccdex-worker-mcp` clients, the `ccdex-gpt-only.sh` hook, the
  `ccdex-worker` MCP registration) keep their historical names on purpose —
  they identify what a machine-convergence pass removes, not the live route,
  and are out of scope for this rename.
- No behavior change; this is naming only.

## RENAMED

- Dedicated compatibility route: `/v1/ccdex/messages` -> `/v1/ccgpt/messages`
  (and its `/count_tokens` sibling). No compatibility alias is retained for
  the old path (hard cut).

## Capabilities

### Modified Capabilities

- `claude-harness-codex`: the alias/bridge route naming moves from CCDEX to
  CCGPT.

## Impact

- Affected code: `app/modules/proxy/api.py`,
  `app/modules/proxy/claude_codex_bridge.py`, `clients/claude-lb-launch`.
- Affected specs: `claude-harness-codex`, `account-routing`,
  `runtime-portability`, `startup-performance` (route/profile naming only —
  the already-retired `ccdex` launcher and worker-transport requirements are
  untouched here; they are removed by the separate, still-pending
  `retire-codex-dispatch-clients` sync).
- Affected tests: `tests/integration/test_ccgpt_proxy.py` (renamed from
  `test_ccdex_proxy.py`), `tests/unit/test_claude_lb_launch.py`,
  `tests/unit/test_anthropic_proxy_api.py`,
  `tests/unit/test_claude_codex_bridge.py`,
  `tests/unit/test_startup_benchmark.py`.
- Not affected: the retired-artifact cleanup strings in
  `scripts/install-claude-clients.sh`, `config/coding-agents/`, and
  `tests/unit/test_install_claude_clients.py`.
