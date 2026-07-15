# Serve Sol worker model aliases on the Messages route

## Why

The owner's routing northstar runs Claude Code as the single harness with
Fable driving and GPT Sol subagents doing implementation (medium effort) and
adversarial verification (xhigh effort). Claude Code subagent definitions can
pin any model ID, and every session already points at agent-lb — so the proxy
can route per-request by model string. The CCDEX bridge (locked Sol profile)
already exists server-side; it was only reachable via the retired `ccdex`
launcher path.

## What Changes

- `/v1/messages` recognizes Sol worker aliases (`gpt-5.6-sol`, plus
  `-low/-medium/-high/-xhigh` effort-pinned variants) and serves them through
  the existing CCDEX bridge instead of the Anthropic pool. Alias-pinned effort
  overrides the request's own effort; the plain alias defers to
  `output_config.effort` (bridge default high). Service tier stays locked to
  `priority`.
- `/v1/messages/count_tokens` returns the local estimate for alias models and
  never selects an Anthropic account.
- `/v1/ccdex/messages` behavior is unchanged; it now shares one helper with
  the alias path.

## Impact

- Affected specs: `claude-harness-codex` (new requirement: worker model
  aliases on the Messages route).
- Affected code: `app/modules/proxy/api.py`, `tests/integration/test_ccdex_proxy.py`.
- Enables: Claude Code subagents pinned via frontmatter (`model:
gpt-5.6-sol-medium` implementer, `gpt-5.6-sol-xhigh` verifier) with zero
  client changes.
