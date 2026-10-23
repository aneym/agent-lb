## 1. Implementation

- [x] 1.1 Accept typed server tools without `input_schema` in `AnthropicToolDefinition`; keep `input_schema` required for custom tools.
- [x] 1.2 Reject server tools on `/v1/ccdex/messages` with a `400` `invalid_request_error` before translation.

## 2. Verification

- [x] 2.1 `/v1/messages` forwards a `web_search_20250305` tool unchanged to the service layer.
- [x] 2.2 `/v1/ccdex/messages` returns `400` naming the server tool and makes no upstream call.
- [x] 2.3 Existing Anthropic and ccdex integration tests pass; `ruff check app clients` passes.
