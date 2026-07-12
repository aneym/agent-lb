## MODIFIED Requirements

### Requirement: Context overflow surfaces as a Claude Code compaction trigger

When an upstream Responses inference fails because the request exceeds the model context window (an upstream `context_length_exceeded` code, or a context-window/token-limit failure message), the ccdex bridge MUST translate the failure into an Anthropic-native `invalid_request_error` whose message contains the phrase `prompt is too long`. It MUST NOT surface the overflow as a generic `api_error`, as a normal (empty) assistant success, or as any code the Claude Code harness classifies as retryable (for example `overloaded_error`).

The delivery channel is load-bearing: Claude Code only reactive-compacts when the failure is received as a non-200 HTTP response **before** it creates the assistant turn. Therefore a terminal overflow that occurs before any assistant content block MUST be surfaced as a non-200 HTTP error response (HTTP 400 for context overflow) carrying the Anthropic error envelope, and MUST NOT be surfaced as an HTTP 200 Server-Sent Events stream beginning with `message_start`. This holds whether the overflow is reported as the pre-stream non-streaming error response OR as an in-band terminal frame (a `response.failed` event or a top-level Codex `error` frame) that arrives before any content — the latter can slip past the pre-stream HTTP probe when a fast `response.created` precedes it.

Only after visible assistant content (a `content_block_start` or `content_block_delta`) has already streamed does a subsequent overflow remain a genuine mid-stream failure: an in-band Anthropic `error` event of type `invalid_request_error` under HTTP 200, emitted with no trailing successful `message_delta` or `message_stop`. The startup peek used to make this distinction MUST be bounded — buffering only until the first content frame or the first terminal error frame — so it does not delay the first visible token or buffer the stream unboundedly. Non-overflow upstream errors MUST retain their existing translation.

#### Scenario: Pre-stream context overflow

- **GIVEN** a `/v1/ccdex/messages` turn whose input exceeds the model context window
- **WHEN** the upstream returns a `context_length_exceeded` error before streaming begins
- **THEN** the endpoint returns an Anthropic error envelope of type `invalid_request_error` whose message contains `prompt is too long`
- **AND** the upstream HTTP status is preserved

#### Scenario: Pre-content in-band context overflow

- **GIVEN** a `/v1/ccdex/messages` turn whose stream has emitted `response.created` but no assistant content
- **WHEN** the upstream stream then emits a `context_length_exceeded` `response.failed` failure
- **THEN** the endpoint returns an HTTP 400 Anthropic error envelope of type `invalid_request_error` whose message contains `prompt is too long`
- **AND** the response body contains no `message_start`

#### Scenario: Pre-content top-level Codex overflow frame

- **GIVEN** a `/v1/ccdex/messages` turn whose stream has emitted `response.created` but no assistant content
- **WHEN** the ChatGPT-backed Codex upstream emits a terminal `error` frame carrying its detail fields on the event root (`code` / `message` / `error_type`) with `code` `context_length_exceeded`
- **THEN** the endpoint returns an HTTP 400 Anthropic error envelope of type `invalid_request_error` whose message contains `prompt is too long`

#### Scenario: Mid-stream context overflow after content

- **GIVEN** a `/v1/ccdex/messages` turn that has already streamed assistant content
- **WHEN** the upstream stream emits a `context_length_exceeded` failure
- **THEN** the bridge emits an in-band Anthropic `error` event of type `invalid_request_error` whose message contains `prompt is too long` under HTTP 200
- **AND** the bridge emits no trailing successful `message_delta` or `message_stop`

#### Scenario: Non-overflow upstream error is unchanged

- **GIVEN** a `/v1/ccdex/messages` turn
- **WHEN** the upstream returns a non-overflow failure (for example a generic response failure)
- **THEN** the bridge surfaces it as an `api_error` carrying the upstream message
