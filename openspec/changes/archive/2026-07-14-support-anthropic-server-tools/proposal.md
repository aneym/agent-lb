## Why

Claude Code's WebSearch tool sends a versioned Anthropic-defined server tool (for example `{"type": "web_search_20250305", "name": "web_search", "max_uses": 8}`) on the normal `/v1/messages` route. The proxy's request model required `input_schema` on every tool, so these requests failed validation and never reached upstream. On the Codex bridge the translation only knows function tools and would silently drop a server tool, returning a response that ignored the tool the caller asked for.

## What Changes

- The Messages request model accepts tools with a `type` other than `custom` without an `input_schema`; they pass through upstream unchanged.
- Custom tools still require `input_schema`.
- `/v1/ccdex/messages` rejects requests carrying server tools with HTTP 400 `invalid_request_error` instead of dropping them.

## Impact

- `app/core/anthropic/models.py`, `app/modules/proxy/api.py`.
- New capability spec `anthropic-messages-compat`.
