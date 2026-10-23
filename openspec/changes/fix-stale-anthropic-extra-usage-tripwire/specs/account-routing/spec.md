## ADDED Requirements

### Requirement: Anthropic extra-usage tripwire applies only while the primary window is exhausted

Anthropic selection MUST NOT exclude an account solely because of an active
extra-usage tripwire cooldown when the account's latest primary usage snapshot
shows subscription headroom, meaning utilization below 100 percent or an
exhausted window whose reset time has already passed. Selection MUST keep
excluding an account with an active tripwire when its latest primary snapshot
reports the window exhausted with a future reset, or when no primary
utilization is known.

#### Scenario: Stale tripwire on a recovered account

- **GIVEN** an Anthropic account with an active extra-usage tripwire cooldown
- **AND** its latest primary usage snapshot is below 100 percent
- **WHEN** a request is routed
- **THEN** the account is eligible for selection

#### Scenario: Tripwire on an exhausted account still blocks

- **GIVEN** an Anthropic account with an active extra-usage tripwire cooldown
- **AND** its latest primary usage snapshot is at 100 percent with a future reset
- **WHEN** a request is routed
- **THEN** the account is not selected

#### Scenario: Tripwire with unknown primary state still blocks

- **GIVEN** an Anthropic account with an active extra-usage tripwire cooldown
- **AND** no primary usage snapshot for the account
- **WHEN** a request is routed
- **THEN** the account is not selected
