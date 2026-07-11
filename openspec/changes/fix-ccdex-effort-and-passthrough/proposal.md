# Fix ccdex per-task effort and Claude-model passthrough

## Why

The initial ccdex bridge locked every request to `gpt-5.6-sol` / `high` / `priority`, which broke two Claude Code behaviors: subagents that declare their own reasoning effort (`effort:` frontmatter, per-task `output_config.effort`) were silently flattened to `high`, and subagents pinned to Claude models (Fable planning, Opus frontend) were translated to Sol instead of reaching the Anthropic route. The launcher also rewrote every `/v1/messages` request unconditionally, so no Claude-model traffic could pass through at all.

## What Changes

- The bridge honors a supported per-request reasoning effort (`low`, `medium`, `high`, `xhigh`) from the Anthropic `output_config.effort` field, defaulting to `high` for missing or unsupported values. Model and service tier stay locked (`gpt-5.6-sol`, `priority`).
- The ccdex route's locked reasoning-effort accounting follows the translated payload's effort instead of the static constant.
- The `ccdex` launcher rewrites `/v1/messages` to `/v1/ccdex/messages` only when the request body's model is `gpt-5.6-sol`; requests naming other models (Claude Fable, Opus, Sonnet subagents) pass through on the ordinary Anthropic path. Token-count requests always stay on the local ccdex counter.
- The rewrite guard lives inside the launcher helper (`CCDEX_MODE` check), so regular `cc` behavior is provably unchanged by unit test rather than by call-site convention.

## Capabilities

### Modified Capabilities

- `claude-harness-codex`: per-task reasoning effort propagation; selective Claude-model passthrough in the launcher; unchanged regular-cc behavior.

## Impact

Affects `app/modules/proxy/claude_codex_bridge.py`, `app/modules/proxy/api.py`, `clients/claude-lb-launch`, and their focused unit/integration tests. No schema, routing-pool, or ordinary Claude-provider changes.
