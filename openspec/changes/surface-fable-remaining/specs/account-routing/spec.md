## ADDED Requirements

### Requirement: Claude launcher shows Fable remaining percent

The `cc` Claude launcher startup banner MUST include the selected account's
scoped Fable remaining percent when `/api/accounts` enrichment returns an
`additionalQuotas` row with `quotaKey: "anthropic_fable_scoped_weekly"` and a
`primaryWindow.usedPercent` value. The remaining percent MUST be calculated as
`100 - usedPercent`, clamped to the inclusive range 0 through 100. If the scoped
quota row is absent or lacks `primaryWindow.usedPercent`, the launcher MUST keep
the availability-only Fable label.

#### Scenario: Selected account has scoped Fable headroom

- **GIVEN** the launcher claims a Claude route
- **AND** `/api/accounts` returns the selected Anthropic account with
  `fableEligible: true`
- **AND** its `anthropic_fable_scoped_weekly` primary window is 84% used
- **WHEN** the startup banner is printed
- **THEN** it includes a Fable indicator showing 16% left

#### Scenario: Selected account has no scoped Fable headroom

- **GIVEN** the launcher claims a Claude route
- **AND** `/api/accounts` returns the selected Anthropic account with
  `fableEligible: false`
- **AND** its `anthropic_fable_scoped_weekly` primary window is 100% used
- **WHEN** the startup banner is printed
- **THEN** it includes a Fable-out indicator showing 0% left

#### Scenario: Scoped Fable amount is unknown

- **GIVEN** the launcher claims a Claude route
- **AND** `/api/accounts` returns the selected Anthropic account without scoped
  Fable quota data
- **WHEN** the startup banner is printed
- **THEN** it preserves the availability-only Fable label
