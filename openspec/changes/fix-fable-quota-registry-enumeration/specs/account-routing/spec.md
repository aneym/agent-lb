## ADDED Requirements

### Requirement: Accounts API exposes the Fable scoped-weekly quota row

`/api/accounts` MUST include an `additionalQuotas` entry with
`quotaKey: "anthropic_fable_scoped_weekly"` for any Anthropic account that has a
recorded Fable scoped-weekly usage marker, even though that quota key is not
declared in the additional-quota registry. The entry's `primaryWindow.usedPercent`
MUST reflect the most recent recorded marker so clients can derive the Fable
remaining percent as `100 - usedPercent`.

#### Scenario: Account with a recorded Fable scoped-weekly marker

- **GIVEN** an Anthropic account whose latest `anthropic_fable_scoped_weekly`
  primary-window marker records 57% used
- **WHEN** `/api/accounts` assembles the account's `additionalQuotas`
- **THEN** the response includes a quota row with
  `quotaKey: "anthropic_fable_scoped_weekly"` and
  `primaryWindow.usedPercent: 57`

#### Scenario: Account without a Fable scoped-weekly marker

- **GIVEN** an Anthropic account with no recorded `anthropic_fable_scoped_weekly`
  usage rows
- **WHEN** `/api/accounts` assembles the account's `additionalQuotas`
- **THEN** no `anthropic_fable_scoped_weekly` quota row is fabricated for it
