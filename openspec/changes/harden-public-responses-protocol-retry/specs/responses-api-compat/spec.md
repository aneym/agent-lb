## ADDED Requirements

### Requirement: Public responses retry transient protocol-only stream failures before semantic output
When serving an OpenAI SDK-compatible public streaming Responses request, the service MUST treat pre-output lifecycle events as replay-bufferable until semantic output reaches the downstream client. Lifecycle-only events include `response.created`, `response.in_progress`, and `response.queued`. If an attempt emits only lifecycle events and then terminates with a retryable transient failure such as `stream_incomplete`, `upstream_stream_truncated`, `server_error`, or `upstream_request_timeout`, the service MUST retry the attempt within the existing retry budget instead of forwarding the failed lifecycle-only attempt. Once the service forwards any non-lifecycle Responses event downstream, it MUST preserve the existing no-replay rule and surface later failures on the same stream.

#### Scenario: Public stream retries lifecycle-only stream_incomplete
- **GIVEN** a public streaming `POST /v1/responses` request is using the OpenAI SDK-compatible contract
- **AND** the first upstream attempt emits only `response.created` or `response.in_progress`
- **AND** that attempt then emits terminal `response.failed` with `error.code = "stream_incomplete"`
- **WHEN** a retry attempt completes successfully before the request budget is exhausted
- **THEN** the public downstream stream MUST contain the successful retry attempt
- **AND** the public downstream stream MUST NOT contain the failed lifecycle-only attempt

#### Scenario: Backend Codex stream keeps first-event no-replay behavior
- **GIVEN** a `POST /backend-api/codex/responses` stream emits `response.in_progress`
- **WHEN** the same attempt then emits `response.failed`
- **THEN** the backend downstream stream MUST surface that failure
- **AND** the backend downstream stream MUST NOT replay the request on another attempt solely because no text delta was emitted

#### Scenario: Public stream does not replay after semantic output
- **GIVEN** a public streaming `POST /v1/responses` request has forwarded `response.output_text.delta` downstream
- **WHEN** the same upstream attempt later fails with a transient stream error
- **THEN** the service MUST surface the failure on the same downstream stream
- **AND** the service MUST NOT retry or fail over the request after the visible output

#### Scenario: HTTP bridge retries replay-safe first-event timeout before downstream failure
- **GIVEN** a public streaming `POST /v1/responses` request is using the HTTP Responses session bridge
- **AND** the request has no `previous_response_id`, no preferred account owner, and no downstream-visible events
- **WHEN** the selected upstream account accepts `response.create` but produces no first event before the first-event timeout
- **THEN** the service MUST quarantine and exclude that account for the bridge key
- **AND** the service MUST retry the same request on a fresh eligible account before forwarding `bridge_first_event_timeout`
- **AND** if the retry produces a first event, the downstream stream MUST continue without the timeout failure

#### Scenario: HTTP bridge preserves account ownership on first-event timeout
- **GIVEN** a public streaming `POST /v1/responses` request is bound to a previous response or preferred account owner
- **WHEN** the selected upstream account produces no first event before the first-event timeout
- **THEN** the service MUST NOT replay the request on a different account solely because no first event arrived
