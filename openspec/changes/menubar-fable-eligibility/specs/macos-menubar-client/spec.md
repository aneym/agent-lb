## ADDED Requirements

### Requirement: Claude rows show Fable availability

The menubar account list MUST decode the `/api/accounts` `fableEligible` field
and, for Anthropic/Claude account rows, render a compact availability indicator
showing whether that account is currently eligible for Fable-class routing.
Rows with `fableEligible: true` MUST show Fable as available. Rows with
`fableEligible: false` MUST show Fable as out/unavailable. Non-Anthropic rows
or rows where the field is null MUST NOT render a Fable indicator. The
indicator MUST fit inside the existing account row height without shifting the
quota window layout.

#### Scenario: Claude account has Fable headroom

- **GIVEN** `/api/accounts` returns an Anthropic account with `fableEligible: true`
- **WHEN** the menubar account list renders that row
- **THEN** the row shows a compact Fable-available indicator

#### Scenario: Claude account is out of Fable usage

- **GIVEN** `/api/accounts` returns an Anthropic account with `fableEligible: false`
- **WHEN** the menubar account list renders that row
- **THEN** the row shows a compact Fable-out indicator

#### Scenario: Non-Claude account omits Fable state

- **GIVEN** `/api/accounts` returns a non-Anthropic account with `fableEligible: null`
- **WHEN** the menubar account list renders that row
- **THEN** the row does not show a Fable indicator
