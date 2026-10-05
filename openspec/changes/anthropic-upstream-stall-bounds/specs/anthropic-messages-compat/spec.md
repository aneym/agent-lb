## ADDED Requirements

### Requirement: Streamed calls never wait on a silent upstream past a bound

For a streamed `/v1/messages` call, the proxy MUST give up on an upstream attempt that delivers no response body bytes within `anthropic_first_byte_timeout_seconds` of being sent, counting connect retries and the header wait. That attempt MUST be logged with error code `upstream_first_byte_timeout`, MUST count as a transient account error, and the call MUST move to another eligible account at once, because no bytes have reached the client. If no candidate answers, the client MUST get an `overloaded_error`. Once bytes have gone out, a gap longer than `anthropic_stream_idle_timeout_seconds` between upstream chunks MUST end the stream with an `overloaded_error` SSE error event, logged with error code `upstream_stream_idle_timeout`. A bound of 0 disables it. Non-streamed calls MUST NOT be bounded this way.

#### Scenario: A silent upstream moves the call to another account

- **GIVEN** two Anthropic accounts and an upstream that sends nothing for the first call
- **WHEN** a streamed Claude Code `/v1/messages` call goes through the proxy
- **THEN** the client gets 200 and the second account's full event stream within a few bounds' time
- **AND** the request log has an `upstream_first_byte_timeout` row for the first account and a `success` row for the other

#### Scenario: A stream that goes silent ends with a retryable error

- **GIVEN** an upstream that sends `message_start` and then nothing
- **WHEN** the stream-idle bound passes
- **THEN** the client receives `message_start` followed by an `overloaded_error` SSE error event
- **AND** the request log has one `upstream_stream_idle_timeout` row

#### Scenario: A slow non-streamed reply is not cut

- **GIVEN** an upstream that takes longer than the bounds to answer a non-streamed call
- **THEN** the call succeeds
