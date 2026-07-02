## ADDED Requirements

### Requirement: Fable routing keys off the upstream Fable-scoped weekly limit

Fable-class eligibility SHALL use Anthropic's Fable-scoped weekly limit as the authoritative signal when it is available:
the usage payload's `limits[]` entry with `kind="weekly_scoped"` and
`scope.model.display_name` matching Fable is ingested on every usage refresh
and stored per account (`anthropic_fable_scoped_weekly`); an account with a
scoped entry recorded within the last 6 hours is Fable-eligible iff the scoped
`used_percent` is below `ANTHROPIC_FABLE_SCOPED_MAX_USED_PERCENT` (default
100). Overall weekly usage MUST NOT exclude an account whose fresh scoped
signal shows headroom. Accounts without fresh scoped data keep the
overall-weekly heuristic and probe-marker behavior.

#### Scenario: Scoped headroom overrides the overall heuristic

- **GIVEN** an account at 62% overall weekly and 45% Fable-scoped usage
- **WHEN** a Fable-class request is routed
- **THEN** the account is a Fable candidate despite exceeding the overall
  heuristic threshold

#### Scenario: Scoped exhaustion excludes despite overall headroom

- **GIVEN** an account at 30% overall weekly whose Fable-scoped usage is at
  the scoped threshold
- **WHEN** a Fable-class request is routed
- **THEN** the account is excluded from the Fable pool

#### Scenario: Non-Fable traffic drains scoped-exhausted accounts first

- **GIVEN** accounts with fresh scoped data on both sides of the scoped
  threshold
- **WHEN** a non-Fable Anthropic request is routed
- **THEN** the burn-first set is derived from the scoped percents

#### Scenario: Heuristic fallback without scoped data

- **GIVEN** an account whose usage payload carries no Fable-scoped entry or
  whose latest scoped row is older than 6 hours
- **WHEN** Fable eligibility is computed
- **THEN** the overall-weekly threshold and probe markers apply exactly as
  before this change

#### Scenario: Dashboard reflects the scoped signal

- **WHEN** `/api/accounts` renders an Anthropic account with fresh scoped data
- **THEN** `fableEligible` reflects the scoped percent against the scoped
  threshold
