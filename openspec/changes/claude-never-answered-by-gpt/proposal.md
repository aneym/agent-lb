# Claude requests are never answered by GPT

## Why
On an upstream 429, 5xx or an empty Claude pool, agent-lb answered a tagged Claude Opus or Sonnet main thread through the GPT bridge. The caller got GPT text under a Claude request and only a header said so. Pre-launch every seat runs Opus (approval alex-opus-only-prelaunch-2026-11-06), so a silent vendor swap breaks the caller's model choice.

## What Changes
- Remove the cross-vendor stand-in from `/v1/messages`: a Claude-model request fails over across Claude accounts as before, then returns the Claude pool's own error (429 with retry-after when known, or the upstream 5xx).
- Remove the stand-in store, `GET /api/pools/stand-ins`, `route stand-ins`, the `standing_in` field in `route pick`, the open-factory doctor stand-in check, the launcher's herdr-tab intent inference and banner, the `stand_in_rollout` policy key and the routing audit swap counter.
- Launchers stop forwarding `AGENT_LB_INTENT` as `x-agent-lb-intent`; the proxy still drops that header before any upstream call.
- No per-request GPT opt-in is kept: no caller asked for one. Explicit gpt-* model routing and `CLAUDE_LB_CODEX_MODE` are unchanged.

## Impact
Affected specifications: anthropic-messages-compat.
Affected code: Anthropic Messages route, pools API, Claude launcher tags and banners, route CLI, open-factory doctor and run, routing audit, routing policy.
