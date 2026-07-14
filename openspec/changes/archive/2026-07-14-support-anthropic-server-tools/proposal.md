## Why

Claude Code now submits Anthropic-defined server tools such as web search through `/v1/messages`. The proxy currently requires every tool to include a custom-tool `input_schema`, so valid web-search requests fail locally with HTTP 400 before reaching Anthropic.

## What Changes

- Accept Anthropic-defined tool declarations that use a versioned `type` and do not include `input_schema`.
- Preserve client-defined tool validation and pass all accepted tool fields upstream unchanged.
- Add regression coverage for both current web-search variants and the existing custom-tool request shape.

## Capabilities

### New Capabilities

- `anthropic-messages-compat`: Defines compatibility requirements for Anthropic Messages requests, including client and server tool declarations.

### Modified Capabilities

## Impact

- Affects `/v1/messages` request validation in `app/core/anthropic/models.py` and focused Anthropic proxy tests.
- Restores Claude Code WebSearch calls without changing upstream credentials, routing, headers, or response streaming.
- Adds no dependency or database change.
