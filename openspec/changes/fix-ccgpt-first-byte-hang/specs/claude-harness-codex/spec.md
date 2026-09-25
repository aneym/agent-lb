# claude-harness-codex delta

## MODIFIED Requirements

### Requirement: Context overflow surfaces as a Claude Code compaction trigger

When an upstream Responses inference fails because the request exceeds the model context window (an upstream `context_length_exceeded` code, or a context-window/token-limit failure message), the ccgpt bridge MUST translate the failure into an Anthropic-native `invalid_request_error` whose message contains the phrase `prompt is too long`. It MUST NOT surface the overflow as a generic `api_error`, as a normal (empty) assistant success, or as any code the Claude Code harness classifies as retryable (for example `overloaded_error`).

The delivery channel is load-bearing: Claude Code only reactive-compacts when the failure is received as a non-200 HTTP response **before** it creates the assistant turn. Therefore a terminal overflow that occurs before any assistant content block MUST be surfaced as a non-200 HTTP error response (HTTP 400 for context overflow) carrying the Anthropic error envelope, and MUST NOT be surfaced as an HTTP 200 Server-Sent Events stream beginning with `message_start`. This holds whether the overflow is reported as the pre-stream non-streaming error response OR as an in-band terminal frame (a `response.failed` event or a top-level Codex `error` frame) that arrives before any content.

Only after visible assistant content (a `content_block_start` or `content_block_delta`) has already streamed does a subsequent overflow remain a genuine mid-stream failure: an in-band Anthropic `error` event of type `invalid_request_error` under HTTP 200, emitted with no trailing successful `message_delta` or `message_stop`. The startup peek used to make this distinction MUST be bounded—buffering only until the first content frame, the first terminal error frame, or a short content grace (10 seconds) after the first translated frame, whichever comes first—so it does not delay the first visible token, hold the response headers through a long reasoning phase, or buffer the stream unboundedly. An overflow that arrives after the grace has released the headers is delivered in-band like a mid-stream failure. Non-overflow upstream errors MUST retain their existing translation.

#### Scenario: Pre-stream context overflow

- **GIVEN** a `/v1/ccgpt/messages` turn whose input exceeds the model context window
- **WHEN** the upstream returns a `context_length_exceeded` error before streaming begins
- **THEN** the endpoint returns an Anthropic error envelope of type `invalid_request_error` whose message contains `prompt is too long`
- **AND** the upstream HTTP status is preserved

#### Scenario: Pre-content in-band context overflow

- **GIVEN** a `/v1/ccgpt/messages` turn whose stream has emitted `response.created` but no assistant content
- **WHEN** the upstream stream then emits a `context_length_exceeded` `response.failed` failure
- **THEN** the endpoint returns an HTTP 400 Anthropic error envelope of type `invalid_request_error` whose message contains `prompt is too long`
- **AND** the response body contains no `message_start`

#### Scenario: Pre-content top-level Codex overflow frame

- **GIVEN** a `/v1/ccgpt/messages` turn whose stream has emitted `response.created` but no assistant content
- **WHEN** Codex emits a terminal `error` frame whose root `code` is `context_length_exceeded`
- **THEN** the endpoint returns an HTTP 400 Anthropic error envelope of type `invalid_request_error` whose message contains `prompt is too long`

#### Scenario: Mid-stream context overflow after content

- **GIVEN** a `/v1/ccgpt/messages` turn that has already streamed assistant content
- **WHEN** the upstream stream emits a `context_length_exceeded` failure
- **THEN** the bridge emits an in-band Anthropic `error` event of type `invalid_request_error` whose message contains `prompt is too long` under HTTP 200
- **AND** the bridge emits no trailing successful `message_delta` or `message_stop`

#### Scenario: Non-overflow upstream error is unchanged

- **GIVEN** a `/v1/ccgpt/messages` turn
- **WHEN** the upstream returns a non-overflow failure
- **THEN** the bridge surfaces it as an `api_error` carrying the upstream message

## ADDED Requirements

### Requirement: Message threads are refused before upstream

The ccgpt bridge keeps no server-side thread state. A Messages request that carries a `thread` field (Claude Code's message-threads beta, `create` or `continue`) MUST be refused with HTTP 400, an Anthropic `invalid_request_error` envelope whose `error.details.error_code` is `thread_unsupported_request`, and MUST NOT be translated or sent upstream.

#### Scenario: Thread continuation delta

- **GIVEN** a `/v1/messages` request for a GPT alias with `thread: {"type": "continue", ...}` whose messages hold only a `tool_result`
- **WHEN** the route receives it
- **THEN** it returns HTTP 400 with `error.details.error_code` `thread_unsupported_request`
- **AND** no upstream Responses request is made

### Requirement: Bounded first byte

A ccgpt streaming turn MUST either send response headers or fail with an Anthropic error envelope within a bounded time. If upstream produces no frame within 90 seconds, the bridge MUST close the upstream stream and return a non-200 Anthropic `api_error` instead of holding the request open.

#### Scenario: Upstream never answers

- **GIVEN** a ccgpt turn whose upstream accepted the request but emits no event
- **WHEN** 90 seconds pass without a frame
- **THEN** the route returns an Anthropic `api_error` with a non-200 status and the upstream stream is closed

#### Scenario: Long reasoning before content

- **GIVEN** a ccgpt turn whose upstream emitted `response.created` and is still reasoning
- **WHEN** the content grace passes with no content frame
- **THEN** the client receives HTTP 200 headers and `message_start`, and later content streams in order
