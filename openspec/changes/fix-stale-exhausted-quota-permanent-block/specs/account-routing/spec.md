## ADDED Requirements

### Requirement: Stale exhausted quota rows must not exclude accounts indefinitely

A persisted usage or additional-quota row MUST NOT exclude an Anthropic account
from routing indefinitely. At the Anthropic additional-quota cooldown gate and the
primary-window exhaustion prefilter, an exhausted row (`used_percent >= 100`)
whose `reset_at` is `None` MUST NOT be treated as an active exclusion; the account
MUST remain eligible. Only an exhausted row with a bounded `reset_at` that is
still in the future continues to gate the account, and its reset MUST contribute
to the reported next-reset metadata. The dashboard additional-quota window
mapping MUST NOT present an exhausted `None`-reset row as permanently consumed; it
MUST render as re-admitted (`used_percent = 0`, `reset_at = None`), the same way
an already-elapsed bounded reset is presented. These gates apply only to Anthropic
additional-quota windows and the `primary` usage window; free-account monthly
quota is unaffected.

#### Scenario: Additional-quota cooldown with a None reset re-admits the account

- **GIVEN** an Anthropic account whose latest additional-quota row for the
  requested quota key reports `used_percent = 100` and `reset_at = None`
- **AND** the account is otherwise routable
- **WHEN** the model-quota eligibility prefilter runs
- **THEN** the account is returned as an eligible candidate
- **AND** it is not counted as a blocked account

#### Scenario: Primary window with a None reset re-admits the account

- **GIVEN** an Anthropic account whose latest `primary` usage window reports
  `used_percent = 100` and `reset_at = None`
- **AND** the account is otherwise routable
- **WHEN** the model-quota eligibility prefilter runs
- **THEN** the account is returned as an eligible candidate
- **AND** it is not counted as a blocked account

#### Scenario: Bounded still-future exhaustion still gates the account

- **GIVEN** an Anthropic account whose latest additional-quota row reports
  `used_percent = 100` and a bounded `reset_at` in the future
- **WHEN** the model-quota eligibility prefilter runs
- **THEN** the account is excluded as a blocked candidate
- **AND** its reset contributes to the reported next-reset metadata

#### Scenario: Dashboard does not display a permanent 100% for a None-reset row

- **GIVEN** an account whose additional-quota window reports `used_percent = 100`
  and `reset_at = None`
- **WHEN** the account's additional-quota window is mapped for display
- **THEN** the window renders `used_percent = 0` with `reset_at = None`
