## ADDED Requirements

### Requirement: Pool metrics strip shows the plan value multiple

The menubar pool metrics strip MUST show, when computable, a value-multiple
line: the API-equivalent retail value of the tokens burned in the weekly
window divided by the prorated weekly cost of the plans backing the pool. The
multiple MUST be visually emphasized by weight only (never color), and the
line MUST be prefixed `≈` whenever any counted account's plan price came from
the client-side list-price fallback rather than an operator-entered amount.

#### Scenario: Multiple renders with the correct math

- **GIVEN** a pool of 3 headline-countable accounts — two Claude Max priced
  from the list-price table at $200/mo each, one Codex Pro with an
  operator-entered `subscription.amount` of $200 (USD) — and
  `summary.cost.totalUsd7d` of $6,200
- **WHEN** the pool metrics strip renders
- **THEN** the weekly plan cost is `(200 + 200 + 200) * 7 / 30.4375 ≈ $137.99`
- **AND** the multiple is `6200 / 137.99 ≈ 44.9`
- **AND** the line renders `≈45× value · $6.2k vs $138/wk` (estimated prefix
  present because two of the three accounts used the fallback table)

#### Scenario: Fully operator-priced pool omits the estimate marker

- **GIVEN** every counted account has an operator-entered USD
  `subscription.amount`
- **WHEN** the multiple is computed
- **THEN** the line has no `≈` prefix

#### Scenario: Line is absent when unpriceable

- **WHEN** `summary.cost.totalUsd7d` is nil or ≤ 0, OR no headline-countable
  account has a resolvable monthly price (operator amount or list-price
  table)
- **THEN** the value-multiple line does not render and contributes zero lines
  to the metrics strip

#### Scenario: Denominator excludes unusable accounts

- **GIVEN** an account that is subscription-canceled, deactivated, or
  `reauth_required` (i.e. not `isHeadlineCountable`)
- **WHEN** the weekly plan cost is summed
- **THEN** that account's plan price is excluded from the denominator
  regardless of what plan type it has on file

#### Scenario: Value multiple is pool-global regardless of provider scope

- **GIVEN** the menubar provider scope is set to a single provider (e.g.
  Claude-only)
- **WHEN** the value-multiple line renders
- **THEN** both the value numerator and the plan-cost denominator still sum
  across every provider's headline-countable accounts, not just the scoped
  provider

#### Scenario: Panel height accounts for the extra line deterministically

- **GIVEN** a computable value multiple
- **WHEN** `PanelLayout` computes the panel height
- **THEN** `metricsLines` includes the value-multiple line as a fixed
  additional `PanelMetrics.metricsLine` height, with no measured/dynamic
  sizing

### Requirement: Privacy mode redacts identity, never aggregates

The menubar app MUST provide a persisted privacy mode that replaces
identity-revealing text — account display names and duplicate-disambiguation
tags, the remote host chip, and account names in pool tooltips — with stable
pseudonyms, while never redacting aggregate pool numbers, and MUST NOT change
panel layout when toggled.

#### Scenario: Toggle affordances are available and persisted

- **GIVEN** the menubar panel is open
- **WHEN** the user clicks the header eye/eye.slash button, or toggles "Hide
  Sensitive Info" in the overflow menu
- **THEN** privacy mode flips for both affordances simultaneously (shared
  `@AppStorage("privacyMode")` state)
- **AND** the setting persists across app relaunches

#### Scenario: Pseudonyms are stable and per-provider numbered

- **GIVEN** privacy mode is enabled and the account list contains accounts
  from both providers
- **WHEN** pseudonyms are built
- **THEN** each account is assigned a pseudonym of the form `"<Provider>
  <n>"` (e.g. `"Claude 1"`, `"Codex 2"`) where `<n>` is assigned by iterating
  accounts sorted by `accountId`, numbered independently per provider
- **AND** a given account's pseudonym is unchanged across a data refresh, a
  change in accounts-list sort order, or a change in provider scope, as long
  as the underlying account list is unchanged

#### Scenario: Remote host is redacted

- **GIVEN** privacy mode is enabled and the app is connected to a remote host
- **WHEN** the header host chip renders
- **THEN** it shows `"remote"` instead of the real short hostname

#### Scenario: Pool tooltip account names are redacted

- **GIVEN** privacy mode is enabled
- **WHEN** the pool card's reset-schedule tooltip renders per-account lines
- **THEN** each line shows the account's pseudonym instead of its real
  display name

#### Scenario: Aggregate numbers are never redacted

- **GIVEN** privacy mode is enabled
- **WHEN** any usage percentage, cost figure, token count, or the §11 value
  multiple renders anywhere in the panel
- **THEN** the number is shown exactly as it would be with privacy mode
  disabled

#### Scenario: Redaction is layout-neutral

- **GIVEN** privacy mode is toggled on or off
- **WHEN** the panel re-renders
- **THEN** `PanelLayout`'s computed panel height is unchanged (pseudonyms and
  redacted text truncate within existing row/line frames; toggling privacy
  mode is not a `PanelLayout.Inputs` field)
