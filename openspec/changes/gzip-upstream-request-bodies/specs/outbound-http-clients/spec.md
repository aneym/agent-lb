## ADDED Requirements

### Requirement: Large model-call request bodies go upstream gzipped

When agent-lb forwards a JSON request body whose compact serialization (no whitespace between tokens) is 16 KiB or more to Anthropic's Messages or count_tokens endpoint, or to the Codex `/codex/responses` or `/codex/responses/compact` endpoint over HTTP, it MUST send the body gzip-compressed with `Content-Encoding: gzip`, and the decompressed body MUST be the same JSON request. Bodies whose compact serialization is under 16 KiB MUST keep the existing uncompressed send, and so MUST requests to other Anthropic-compatible providers (GLM, Kimi) and websocket transport. Compression MUST NOT run on the event loop. Setting `AGENT_LB_UPSTREAM_REQUEST_GZIP_ENABLED=false` MUST turn compression off, so every body goes out uncompressed. Compression time MUST count against the request's budget: if the budget runs out while the body waits for or undergoes compression, the request MUST fail with `upstream_request_timeout` before any byte is uploaded.

#### Scenario: A large Messages request is gzipped on the wire

- **GIVEN** an active Anthropic account
- **WHEN** a client posts a `/v1/messages` request whose compact JSON body is 16 KiB or more
- **THEN** the upstream receives `Content-Encoding: gzip`
- **AND** the gzipped body decodes to the forwarded request

#### Scenario: A large Codex responses stream is gzipped on the wire

- **WHEN** agent-lb streams a Codex responses request whose compact JSON body is 16 KiB or more over HTTP
- **THEN** the upstream receives `Content-Encoding: gzip`
- **AND** the gzipped body decodes to the forwarded request

#### Scenario: The off switch sends large bodies uncompressed

- **GIVEN** `AGENT_LB_UPSTREAM_REQUEST_GZIP_ENABLED=false`
- **WHEN** agent-lb streams a Codex responses request whose compact JSON body is 16 KiB or more over HTTP
- **THEN** the upstream receives the body with no `Content-Encoding`

#### Scenario: A budget spent waiting for compression fails before upload

- **GIVEN** every compression worker is busy
- **WHEN** a large Codex responses request's budget runs out before its body is compressed
- **THEN** the stream fails with `upstream_request_timeout` at its deadline
- **AND** the upstream receives no request
