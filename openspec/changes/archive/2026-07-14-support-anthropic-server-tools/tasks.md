## 1. Request model

- [x] 1.1 Accept tool definitions with a non-`custom` `type` and no `input_schema` in `AnthropicToolDefinition`.
- [x] 1.2 Keep `input_schema` required for custom tools.

## 2. Codex bridge

- [x] 2.1 Reject server tools on `/v1/ccdex/messages` with HTTP 400 `invalid_request_error` before translation.

## 3. Validation

- [x] 3.1 Run the acceptance tests for server-tool passthrough and bridge rejection, ruff, and strict OpenSpec validation.
