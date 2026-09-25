## ADDED Requirements

### Requirement: A bridged Claude turn never surfaces an account rejection while another account is usable

When the Codex account chosen for a bridged `/v1/messages` turn rejects the connection (HTTP 401/403) or its token cannot be refreshed, the bridge MUST exclude that account, pick another usable account, re-pin the conversation there and retry the turn. If no account accepts the turn, the client MUST receive a retryable 5xx error, never a 401 or 403 that Claude Code shows as "Please run /login".

#### Scenario: The pinned account returns 403

- **WHEN** the first account's upstream connection returns 403 and a second account is usable
- **THEN** the turn completes on the second account with HTTP 200

#### Scenario: No account accepts the turn

- **WHEN** every eligible account rejects the connection
- **THEN** the client receives HTTP 503 with an Anthropic `api_error` body

### Requirement: Bridged Claude streams report usage the way Anthropic does

A bridged stream's `message_start` MUST carry an estimate of the input tokens. Its final `message_delta` MUST carry cumulative `input_tokens` (excluding cached tokens), `cache_read_input_tokens`, `cache_creation_input_tokens` and `output_tokens`.

#### Scenario: A cached turn

- **WHEN** the upstream reports 8258 input tokens of which 7424 were cached, and 363 output tokens
- **THEN** `message_delta` usage is 834 input, 7424 cache read, 0 cache creation and 363 output
