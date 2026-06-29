## ADDED Requirements

### Requirement: Anthropic model-quota selection failures are diagnosable

When Anthropic account selection fails after model-quota eligibility filtering, the proxy MUST include diagnostics that identify the requested quota key, how many accounts were excluded by that quota key, how many candidates remained after the prefilter, and the selector failure reason when available. The response MUST preserve retry metadata derived from the quota reset and MUST keep the provider-boundary message that OpenAI accounts cannot serve Claude routing.

#### Scenario: active account excluded by requested model quota

- **GIVEN** at least one active Anthropic account is excluded by a cooldown for the requested quota key
- **AND** remaining Anthropic accounts are not selectable
- **WHEN** a Claude session route is requested for that quota key
- **THEN** the error message includes the account status summary
- **AND** the error message includes model-quota prefilter diagnostics for that quota key
- **AND** the error response includes `retryAt` and `retryAfterSeconds` when a reset time is known
- **AND** the error message states that OpenAI accounts are not eligible for Claude routing
