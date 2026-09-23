## ADDED Requirements

### Requirement: Status reports cached banked reset credits per account
The read-only status CLI MUST include a nullable `reset_credits_available` value for each account in JSON, copied from that account's `resetCreditsAvailable` field in `GET /api/accounts`. A missing, null, or invalid count MUST remain unknown rather than becoming zero. Human output MUST distinguish unknown, zero, and positive banked-reset counts. The command MUST NOT make an additional upstream credit-listing request or redeem credits.

#### Scenario: Known and unknown banked counts
- **GIVEN** account summaries with positive, zero, and null `resetCreditsAvailable` values
- **WHEN** an operator runs `agent-lb status`
- **THEN** each account's JSON and human output reflect its own count without conflating null with zero

### Requirement: Human status labels model-scoped Fable telemetry accurately
Human status output MUST label Fable-scoped weekly telemetry only when the requested model is Fable. JSON MUST retain each account's existing `fable` object for compatibility. The CLI MUST recognize `opus` and `sonnet` aliases as Anthropic models.

#### Scenario: Opus model assessment
- **WHEN** an operator requests `--model claude-opus-5-5` or `--model opus`
- **THEN** human account rows do not present Fable-scoped quota as part of that assessment

#### Scenario: Fable model assessment
- **WHEN** an operator requests a Fable model
- **THEN** human account rows label its Fable-scoped weekly observation
