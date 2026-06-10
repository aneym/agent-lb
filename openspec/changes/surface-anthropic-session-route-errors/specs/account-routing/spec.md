## ADDED Requirements

### Requirement: Anthropic session-route surfaces selection failures
The `/api/anthropic/session-route` endpoint MUST return an OpenAI-compatible error envelope that preserves the Anthropic account-selection failure code and message when no account can be claimed for a Claude session.

#### Scenario: No Anthropic account can be selected
- **GIVEN** the launcher asks agent-lb to claim a Claude session route
- **AND** no Anthropic account is selectable for the requested quota
- **WHEN** `/api/anthropic/session-route` handles the claim
- **THEN** the response status matches the account-selection failure status
- **AND** `error.message` describes the account-selection failure
- **AND** `error.code` identifies the account-selection failure code
- **AND** `error.message` is not replaced by a generic request failure message

### Requirement: Claude launcher formats selection failures
The Claude launcher MUST print a concise human-readable account-selection failure summary when `/api/anthropic/session-route` returns an OpenAI-compatible error envelope.

#### Scenario: Session-route returns no selectable Anthropic account
- **GIVEN** `/api/anthropic/session-route` returns an error envelope describing Anthropic account statuses
- **WHEN** the launcher reports the preflight failure
- **THEN** the launcher output includes the account status summary
- **AND** the launcher output does not print the raw JSON envelope
