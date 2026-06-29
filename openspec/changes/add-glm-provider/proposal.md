# add-glm-provider

## Why
agent-lb can already pool OpenAI/Codex and Anthropic/Claude accounts, but GLM
Coding Plan traffic still has to bypass the local balancer and point Claude Code
directly at Z.AI. That loses the same account inventory, sticky routing, request
logs, and quota-cooldown behavior operators use for the rest of the local pool.

## What Changes
- Add a GLM provider that is imported with an API key instead of OAuth.
- Store GLM token material encrypted and keep API keys out of responses and audit
  details.
- Route Anthropic-compatible `/v1/messages` requests whose model starts with
  `glm-` to Z.AI's Anthropic-compatible endpoint using only GLM accounts.
- Keep GLM quota cooldowns and sticky-session keys provider-scoped so they cannot
  collide with Claude/Anthropic routing.
- Point the local `glm` shell command at agent-lb with GLM model defaults.

## Impact
- Operators can use `glm` through the same local agent-lb service as `cclb`.
- Existing OpenAI/Codex and Anthropic/Claude routing remains provider-filtered.
- GLM usage/cooldown evidence is visible through the same additional-quota
  machinery used by Claude Messages routing.
