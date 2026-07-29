# frontend-architecture

## ADDED Requirements

### Requirement: Fable scoped weekly limit is a first-class account window

For Anthropic accounts that report the Fable-scoped weekly limit
(`additionalQuotas` entry with quota key `anthropic_fable_scoped_weekly`),
the web frontend MUST present the Fable window alongside the Session and
Week windows: as a third meter in the accounts-page list rows, as a promoted
row in the account detail usage panel (removed from the generic
additional-quotas list to avoid duplication), and as a third quota bar on
dashboard account cards. The row/bar label MUST indicate ineligibility
("Fable · out") when the account is not Fable-eligible. Accounts without the
scoped window (including all openai accounts) MUST render without any Fable
UI.

#### Scenario: anthropic account with the scoped window

- **GIVEN** an anthropic account whose additionalQuotas include
  `anthropic_fable_scoped_weekly` with 38% used
- **WHEN** the accounts list row, account usage panel, or dashboard account
  card renders
- **THEN** a Fable meter/bar shows 62% remaining with the window's reset

#### Scenario: openai account

- **WHEN** an openai account renders in any of those views
- **THEN** no Fable meter or bar appears

#### Scenario: ineligible account

- **GIVEN** an anthropic account with `fableEligible: false`
- **WHEN** the usage panel or dashboard card renders its Fable row
- **THEN** the label reads "Fable · out"

### Requirement: Dashboard reports pool-level Fable runway

The dashboard MUST show a "Fable runway (weekly scoped)" stat when at least
one routable Anthropic account (status not paused, deactivated, or
reauth-required) reports the Fable-scoped weekly window. The value is the
mean Fable remaining percent across those reporters —
exhausted-but-routable accounts stay in the denominator — and the meta line
reports Fable-eligible versus total reporting accounts.

#### Scenario: mixed pool

- **GIVEN** two routable anthropic accounts reporting 62% and 40% Fable
  remaining and any number of openai accounts
- **WHEN** the dashboard stats build
- **THEN** the Fable runway stat shows 51% across 2 accounts

#### Scenario: no reporters

- **GIVEN** no anthropic account reports the scoped window
- **WHEN** the dashboard stats build
- **THEN** no Fable runway stat appears
