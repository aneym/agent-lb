## ADDED Requirements

### Requirement: Context overflow surfaces as a Claude Code compaction trigger

When an upstream Responses inference fails because the request exceeds the model context window (an upstream `context_length_exceeded` code, or a context-window/token-limit failure message), the ccdex bridge MUST translate the failure into an Anthropic-native `invalid_request_error` whose message contains the phrase `prompt is too long`, preserving the upstream HTTP status. It MUST NOT surface the overflow as a generic `api_error`, as a normal (empty) assistant success, or as any code the Claude Code harness classifies as retryable (for example `overloaded_error`). This applies to both the pre-stream non-streaming error response and the mid-stream `response.failed`/`error` event surfaces of the bridge. Non-overflow upstream errors MUST retain their existing translation.

#### Scenario: Pre-stream context overflow

- **GIVEN** a `/v1/ccdex/messages` turn whose input exceeds the model context window
- **WHEN** the upstream returns a `context_length_exceeded` error before streaming begins
- **THEN** the endpoint returns an Anthropic error envelope of type `invalid_request_error` whose message contains `prompt is too long`
- **AND** the upstream HTTP status is preserved

#### Scenario: Mid-stream context overflow

- **GIVEN** a `/v1/ccdex/messages` turn that has started streaming
- **WHEN** the upstream stream emits a `context_length_exceeded` failure
- **THEN** the bridge emits an Anthropic `error` event of type `invalid_request_error` whose message contains `prompt is too long`

#### Scenario: Mid-stream context overflow via top-level error frame

- **GIVEN** a `/v1/ccdex/messages` turn that has started streaming
- **WHEN** the ChatGPT-backed Codex upstream emits a terminal `error` frame that carries its detail fields on the event root (`code` / `message` / `error_type`) rather than under an `error` object or `response.error`
- **THEN** the bridge still classifies the overflow and emits an Anthropic `error` event of type `invalid_request_error` whose message contains `prompt is too long`
- **AND** a non-overflow top-level `error` frame is surfaced as an `api_error` carrying the upstream message

#### Scenario: Non-overflow upstream error is unchanged

- **GIVEN** a `/v1/ccdex/messages` turn
- **WHEN** the upstream returns a non-overflow failure (for example a generic response failure)
- **THEN** the bridge surfaces it as an `api_error` carrying the upstream message
