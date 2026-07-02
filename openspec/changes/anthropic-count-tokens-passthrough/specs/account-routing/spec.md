## ADDED Requirements

### Requirement: Anthropic count_tokens passthrough
The service MUST accept `POST /v1/messages/count_tokens` and forward the raw JSON body verbatim to the selected account's Anthropic-compatible upstream `/v1/messages/count_tokens` endpoint, returning the upstream status code and body unchanged, including Anthropic error envelopes. The forwarded request MUST use the selected account's bearer token and the same Anthropic headers as `/v1/messages` forwarding, including the OAuth beta header.

The route MUST validate the proxy API key and per-key model access the same way `/v1/messages` does. Because token counting is quota-free upstream, the path MUST NOT create api-key usage reservations, MUST NOT settle usage, and MUST NOT write request logs, quota cooldowns, or response-driven account error-health state. Credential-refresh failures are exempt: the shared token-refresh path MAY mark an account permanently failed exactly as it does on every other route, because that verdict is credential-driven, not count-response-driven.

Token counting is read-only and account-agnostic, so selection MAY use any active provider-matching account without sticky-session affinity. Serving a count from a different account than a session's sticky messages account MUST NOT alter sticky state or messages routing, and message-quota cooldowns MUST NOT exclude accounts from serving counts.

When no account can be selected, the service MUST return the Anthropic-style error envelope (`{"type": "error", "error": {...}}`) with the selection failure code instead of `405 Method Not Allowed`.

#### Scenario: Claude Code counts tokens through the proxy
- **GIVEN** an active Anthropic OAuth account
- **WHEN** a client sends `POST /v1/messages/count_tokens` with a Messages-shaped JSON body
- **THEN** the service forwards the body verbatim to the upstream count endpoint with the account bearer token and OAuth beta header
- **AND** returns the upstream status and JSON body unchanged
- **AND** no api-key reservation, usage settlement, request log, or response-driven error-health write occurs

#### Scenario: Upstream count error passes through without health writes
- **WHEN** the upstream count endpoint returns an error status with an Anthropic error envelope
- **THEN** the service returns that status and envelope verbatim
- **AND** the serving account's status and quota cooldowns are unchanged

#### Scenario: Selection failure returns an Anthropic error envelope
- **GIVEN** no active Anthropic accounts exist
- **WHEN** a client sends `POST /v1/messages/count_tokens`
- **THEN** the service returns an Anthropic-style error envelope with the selection failure code
- **AND** the response is not `405 Method Not Allowed`
