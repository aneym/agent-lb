## ADDED Requirements

### Requirement: Claude-model requests are answered only by Claude
The Messages handler SHALL serve a request for a Claude model only from Claude accounts. When every eligible Claude account fails with HTTP 429, HTTP 500 or greater, or a no_available_ error code, the handler SHALL return that Anthropic-format error to the caller, with retry-after when a reset time is known, and SHALL NOT answer through the GPT bridge or any other vendor. Request headers such as x-agent-lb-intent or x-agent-lb-lane SHALL NOT change the vendor that answers.

#### Scenario: Exhausted Claude pool under a tagged main thread
- **GIVEN** a Claude Opus request carrying x-agent-lb-intent orchestrator and no x-claude-code-agent-id
- **WHEN** every Claude account returns 429
- **THEN** the caller receives HTTP 429 with an Anthropic rate_limit_error body
- **AND** no GPT model is called and no x-agent-lb-standing-in header is set

#### Scenario: Explicit GPT routing is unchanged
- **GIVEN** a Messages request whose model resolves to a gpt-* bridge model
- **WHEN** the request is sent
- **THEN** it is served through the GPT bridge as before
