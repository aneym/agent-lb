## Context

`AnthropicToolDefinition` is the Pydantic model for entries in a Messages request's `tools`. It uses `extra="allow"`, and the Anthropic service forwards `payload.model_dump(exclude_none=True)`, so any field the model accepts reaches upstream untouched. The only blocker for server tools was the required `input_schema`.

## Decisions

- Add an optional `type` field and make `input_schema` optional. A tool is a server tool when `type` is set and is not `custom`. A model validator keeps `input_schema` required for custom tools, so malformed custom tools still fail fast.
- The proxy does not keep an allowlist of server tool versions. Anthropic adds new versions (`web_search_20250305`, `web_fetch_*`, `code_execution_*`) and upstream is the authority on which it accepts; an allowlist would reintroduce this bug on every new version.
- The Codex bridge cannot translate server tools to Responses equivalents faithfully, so `/v1/ccdex/messages` rejects them before translation with a 400 `invalid_request_error` naming the types. Dropping them would return a response that quietly ignored the requested tool.

## Failure modes

- Unknown server tool type on `/v1/messages`: forwarded; upstream returns its own error envelope if it rejects the type.
- Server tool on `/v1/ccdex/messages`: 400 before any upstream call or reservation.
