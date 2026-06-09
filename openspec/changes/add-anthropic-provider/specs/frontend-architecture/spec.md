## ADDED Requirements

### Requirement: Anthropic quota availability is visible in account surfaces
The account list and account detail surfaces SHALL show Anthropic account usage and availability using the best available OAuth-derived usage windows plus locally observed quota-key cooldowns. When true upstream usage percentages are unavailable for a quota key, the UI SHALL represent cooldown or availability evidence honestly instead of implying precise remaining capacity.

#### Scenario: Quota cooldown appears on the account payload
- **GIVEN** an Anthropic account has a recorded cooldown for a top-model quota key
- **WHEN** the dashboard fetches `/api/accounts`
- **THEN** the response includes that cooldown in the account's additional quota data with a human-readable label and reset time

#### Scenario: Request-log evidence remains visible
- **GIVEN** Anthropic requests have succeeded or failed through agent-lb
- **WHEN** an operator inspects Anthropic accounts
- **THEN** the account surfaces include provider identity, request counts, token totals, cache create/read totals, and recent availability evidence
