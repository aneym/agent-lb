## Context

`AnthropicToolDefinition` is the request-side model for every entry in
`tools`. It allowed extra fields but required `input_schema`, which only custom
tools carry.

## Decisions

- Classify by `type`: absent or `custom` is a custom tool and must have
  `input_schema`; any other value is an Anthropic server tool. We do not keep an
  allowlist of known versions, because Anthropic ships new versioned types
  (`web_search_20250305`, `web_fetch_20250910`, `code_execution_20250825`, ...)
  and the passthrough route should honor whatever Anthropic itself accepts.
- Passthrough serializes with `exclude_none`, so a server tool goes upstream with
  exactly the fields the client sent.
- The Codex bridge only translates function tools. It fails closed with a `400`
  `invalid_request_error` listing the server tool types, checked in the route
  before translation and before any upstream call or reservation.

## Failure modes

- A custom tool missing `input_schema` still fails request validation.
- A server tool on the Codex bridge is never dropped; the caller sees the error.
