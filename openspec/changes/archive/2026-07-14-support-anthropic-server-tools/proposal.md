## Why

Claude Code's WebSearch tool sends an Anthropic-defined server tool such as
`{"type": "web_search_20250305", "name": "web_search", "max_uses": 8}` on the
normal `/v1/messages` route. The proxy's tool model required `input_schema` on
every tool, so these requests failed request validation and never reached
Anthropic. The Codex bridge (`/v1/ccdex/messages`) silently dropped any tool
without an `input_schema`, so a caller could get an answer that quietly ignored
the tool it asked for.

## What Changes

- `/v1/messages` accepts versioned Anthropic server tools (any tool `type` other
  than `custom`) without `input_schema` and forwards them to Anthropic unchanged,
  including tool-specific fields.
- Custom tools (no `type`, or `type: "custom"`) still require `input_schema`.
- `/v1/ccdex/messages` rejects a request carrying any server tool with a `400`
  `invalid_request_error` naming the tool type, before any upstream call.

## Impact

- Code: `app/core/anthropic/models.py`, `app/modules/proxy/api.py`.
- Specs: `anthropic-messages-compat`.
