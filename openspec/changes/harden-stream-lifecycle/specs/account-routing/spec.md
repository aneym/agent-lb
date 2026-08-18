## ADDED Requirements

### Requirement: Anthropic in-band stream errors reach account health and request logs

The Anthropic proxy SHALL parse forwarded SSE chunks for in-band `error` events. When a stream that returned HTTP 200 carries an `error` event, the proxy MUST forward the upstream bytes verbatim (a stream that already emitted output is never retried or spliced), MUST record a transient error against the serving account, MUST persist the request log with an error status carrying the upstream error type and message plus any usage collected before the failure, and MUST settle any API-key reservation. The proxy MUST NOT clear quota cooldowns or record a success for such a stream.

#### Scenario: Upstream emits an error event mid-stream

- **GIVEN** an Anthropic upstream responds 200 and streams content followed by an SSE `error` event
- **WHEN** the proxy forwards the stream
- **THEN** the client receives the upstream bytes unchanged
- **AND** the request log records status error with code `stream_error_<type>` and the upstream message
- **AND** the serving account's transient error count increases

#### Scenario: Clean stream is unaffected

- **WHEN** a forwarded stream completes without an in-band `error` event
- **THEN** the request log records success and account transient error state is cleared
