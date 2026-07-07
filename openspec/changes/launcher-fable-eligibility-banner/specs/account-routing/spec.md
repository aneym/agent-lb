## ADDED Requirements

### Requirement: Claude launcher shows Fable availability

The `cc` Claude launcher startup banner MUST surface selected-account Fable
availability when `/api/accounts` enrichment returns a boolean `fableEligible`
field for the selected Anthropic account. If `fableEligible` is true, the banner
MUST indicate Fable is available. If `fableEligible` is false, the banner MUST
indicate Fable is out/unavailable. If `/api/accounts` enrichment is unavailable,
the selected account cannot be matched, or `fableEligible` is null or missing,
the launcher MUST preserve the existing banner without inventing a Fable state.

#### Scenario: Selected account has Fable headroom

- **GIVEN** the launcher claims a Claude route
- **AND** `/api/accounts` returns the selected Anthropic account with
  `fableEligible: true`
- **WHEN** the startup banner is printed
- **THEN** it includes a Fable-available indicator

#### Scenario: Selected account is out of Fable routing headroom

- **GIVEN** the launcher claims a Claude route
- **AND** `/api/accounts` returns the selected Anthropic account with
  `fableEligible: false`
- **WHEN** the startup banner is printed
- **THEN** it includes a Fable-out indicator

#### Scenario: Fable state is unknown

- **GIVEN** the launcher claims a Claude route
- **AND** `/api/accounts` enrichment is unavailable or omits `fableEligible`
- **WHEN** the startup banner is printed
- **THEN** it does not include a Fable availability indicator
