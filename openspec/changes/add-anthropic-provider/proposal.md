# add-anthropic-provider

## Why
codex-lb currently pools ChatGPT/OpenAI accounts only. Operators who also use Claude Code cannot point `ANTHROPIC_BASE_URL` at the same local balancer because there is no Anthropic provider, no Anthropic OAuth flow, and no Messages API proxy path.

The goal is to make one local codex-lb instance manage both account pools while preserving the existing OpenAI/Codex request path for upstream reviewability.

## What Changes
- Add a provider dimension to persisted accounts, request logs, and usage history so OpenAI and Anthropic accounts are selected, logged, and reported separately.
- Introduce a thin provider dispatch seam that wraps the existing OpenAI implementations without changing OpenAI proxy behavior.
- Add Anthropic core protocol support: Messages SSE parsing, cache-aware usage/pricing, model registry sync, OAuth PKCE token handling without `id_token`, and account refresh.
- Add a slim `/v1/messages` Anthropic proxy route that selects only Anthropic accounts, preserves Claude Code request headers and system prompt content, swaps only the account bearer token, and streams upstream SSE unchanged.
- Update the dashboard account and usage surfaces to show provider identity and Anthropic cache token usage alongside OpenAI accounts.

## Impact
- Claude Code can use codex-lb through `ANTHROPIC_BASE_URL` once real Anthropic OAuth accounts are added.
- Existing OpenAI/Codex routes remain behaviorally unchanged and continue using the existing proxy service.
- Schema migration is required; existing rows default to provider `openai`.
- Runtime validation with real Claude accounts remains separate from mocked-upstream code acceptance.
