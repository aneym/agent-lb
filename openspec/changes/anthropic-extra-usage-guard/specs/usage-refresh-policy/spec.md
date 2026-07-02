## ADDED Requirements

### Requirement: Anthropic extra-usage state is ingested and surfaced

The Anthropic usage client MUST parse the `extra_usage` block of the OAuth usage
payload (`is_enabled`, `used_credits`, `monthly_limit`, `utilization`,
`currency`, `decimal_places`) and surface it on account rows through the
existing credits fields: `creditsHas` reflects `is_enabled`, `creditsBalance`
reflects the remaining extra-usage budget in currency units, and
`creditsUnlimited` is false. Accounts without the block MUST keep null credits
fields.

#### Scenario: Extra-usage burn is dashboard-visible

- **GIVEN** an Anthropic account whose usage payload reports `extra_usage.is_enabled: true` with a monthly limit and used credits
- **WHEN** `/api/accounts` renders the account
- **THEN** `creditsHas` is true and `creditsBalance` equals the remaining extra-usage budget

#### Scenario: Accounts without extra usage are unchanged

- **GIVEN** an Anthropic account whose usage payload has `extra_usage` absent or disabled
- **WHEN** `/api/accounts` renders the account
- **THEN** `creditsHas` reflects the disabled state and no fabricated balance appears
