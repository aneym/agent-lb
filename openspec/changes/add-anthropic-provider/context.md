# Context

## Provider Boundary

OpenAI/Codex traffic stays on the existing Responses proxy implementation. Anthropic traffic is routed through a new Messages-specific service so Anthropic requests never pass through OpenAI payload normalization, OpenAI SSE parsing, previous-response handling, websocket bridge logic, or request rewriters.

## Claude Code Compatibility

Claude Code is the real client for this feature. The proxy must preserve the request shape it sends, including `anthropic-beta: oauth-2025-04-20` and the client-authored system prompt prefix. The proxy may replace the inbound bearer token with the selected account token and may add required upstream headers such as `anthropic-version`; it must not strip beta headers or modify message content.

Claude Code/Fable payloads may include `system` role entries inside `messages`, adaptive `thinking`, `context_management`, and `output_config`. The proxy treats these as valid pass-through fields rather than reinterpreting or rejecting them.

Claude Code `/usage` fetches Claude Max utilization from `GET /api/oauth/usage`. For dashboard and routing parity we ingest only the top-model-relevant windows: `five_hour` as the primary session window and `seven_day` as the all-model weekly window. `seven_day_sonnet` is intentionally ignored in this pass so the Anthropic account UI does not imply Sonnet-specific routing decisions.

Claude Code load-balanced launch must preserve the client billing mode. Setting only `ANTHROPIC_BASE_URL` points Claude Code at agent-lb while keeping Claude Max/OAuth billing visible in the client. Setting `ANTHROPIC_AUTH_TOKEN` makes Claude Code switch to API Usage Billing and is not acceptable for the local launcher or default rollout.

Anthropic rate limits are not reliable whole-account status signals. A top-model or thinking `429` can mean the requested quota class is exhausted while the same OAuth account remains usable for other Claude traffic. The selector therefore records local cooldown evidence in additional quota history by Anthropic quota key and filters selection for the requested key instead of marking the account globally `rate_limited`.

Prompt caching remains viable through the proxy only if account locality is preserved. Claude cache state is account-scoped, so repeated turns in the same Claude Code conversation need durable account stickiness until the pinned account becomes unavailable for the requested quota key.

## Data Model

Existing data is OpenAI data. Migrations default legacy rows to `openai`, and OpenAI-only identity fields such as `chatgpt_account_id` and `id_token_encrypted` become nullable because Anthropic OAuth does not return an `id_token`.

Anthropic request usage includes `input_tokens`, `output_tokens`, `cache_creation_input_tokens`, and `cache_read_input_tokens`. Request logs need first-class cache creation/read fields so cost reporting can distinguish Anthropic cache tiers.

## Reviewability

The contribution should be split into two reviewable PRs later:

1. Provider seam refactor with no intended OpenAI behavior change.
2. Additive Anthropic provider implementation on top of that seam.

`GOAL.md` is a local checkpoint ledger for this fork run and does not need to be part of upstream PR branches unless explicitly chosen later.
