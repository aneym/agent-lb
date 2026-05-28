# Context

## Provider Boundary

OpenAI/Codex traffic stays on the existing Responses proxy implementation. Anthropic traffic is routed through a new Messages-specific service so Anthropic requests never pass through OpenAI payload normalization, OpenAI SSE parsing, previous-response handling, websocket bridge logic, or request rewriters.

## Claude Code Compatibility

Claude Code is the real client for this feature. The proxy must preserve the request shape it sends, including `anthropic-beta: oauth-2025-04-20` and the client-authored system prompt prefix. The proxy may replace the inbound bearer token with the selected account token and may add required upstream headers such as `anthropic-version`; it must not strip beta headers or modify message content.

## Data Model

Existing data is OpenAI data. Migrations default legacy rows to `openai`, and OpenAI-only identity fields such as `chatgpt_account_id` and `id_token_encrypted` become nullable because Anthropic OAuth does not return an `id_token`.

Anthropic request usage includes `input_tokens`, `output_tokens`, `cache_creation_input_tokens`, and `cache_read_input_tokens`. Request logs need first-class cache creation/read fields so cost reporting can distinguish Anthropic cache tiers.

## Reviewability

The contribution should be split into two reviewable PRs later:

1. Provider seam refactor with no intended OpenAI behavior change.
2. Additive Anthropic provider implementation on top of that seam.

`GOAL.md` is a local checkpoint ledger for this fork run and does not need to be part of upstream PR branches unless explicitly chosen later.
