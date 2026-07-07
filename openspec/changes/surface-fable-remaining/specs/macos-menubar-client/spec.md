## ADDED Requirements

### Requirement: Claude rows show Fable remaining percent

The macOS menubar account list MUST decode enough of
`/api/accounts.additionalQuotas` to derive scoped Fable remaining percent from
the `anthropic_fable_scoped_weekly` primary window. For Anthropic/Claude account
rows with a boolean `fableEligible` value and scoped Fable `usedPercent`, the
compact Fable indicator MUST include the remaining percent calculated as
`100 - usedPercent`, clamped to the inclusive range 0 through 100. If scoped
Fable quota data is absent, the row MUST keep the existing availability-only
Fable indicator. Non-Anthropic rows MUST NOT show a Fable indicator.

#### Scenario: Claude account has scoped Fable headroom

- **GIVEN** `/api/accounts` returns an Anthropic account with
  `fableEligible: true`
- **AND** its `anthropic_fable_scoped_weekly` primary window is 84% used
- **WHEN** the menubar account list renders that row
- **THEN** the row shows a compact Fable indicator with 16% remaining

#### Scenario: Claude account is out of scoped Fable usage

- **GIVEN** `/api/accounts` returns an Anthropic account with
  `fableEligible: false`
- **AND** its `anthropic_fable_scoped_weekly` primary window is 100% used
- **WHEN** the menubar account list renders that row
- **THEN** the row shows a compact Fable-out indicator with 0% remaining

#### Scenario: Scoped Fable amount is unknown

- **GIVEN** `/api/accounts` returns an Anthropic account without scoped Fable
  quota data
- **WHEN** the menubar account list renders that row
- **THEN** the row keeps the existing availability-only Fable indicator
