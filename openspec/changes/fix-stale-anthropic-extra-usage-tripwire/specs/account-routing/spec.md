## ADDED Requirements

### Requirement: Anthropic extra-usage tripwire expires when the primary window recovers
An active Anthropic extra-usage tripwire cooldown MUST exclude the account from standard routing, including the transient cooldown bypass, until a primary-window snapshot recorded at or after the tripwire shows the window is not actively exhausted. After that, agent-lb MUST NOT exclude the account because of the tripwire. Other quota gates and paid fallback rules MUST NOT change.

#### Scenario: Stale tripwire does not block a recovered account
- **GIVEN** an Anthropic account has an extra-usage tripwire cooldown whose reset is still in the future
- **AND** a primary-window snapshot recorded after the tripwire shows headroom
- **WHEN** an Anthropic request is routed
- **THEN** the account is eligible for standard routing

#### Scenario: Fresh tripwire rotates before the usage refresh
- **GIVEN** an Anthropic response was billed to extra usage and recorded a tripwire
- **AND** no primary-window snapshot newer than the tripwire exists
- **WHEN** the next Anthropic request is routed
- **THEN** the account is excluded from standard routing

#### Scenario: Tripwire still blocks while primary is exhausted
- **GIVEN** an Anthropic account has an active extra-usage tripwire cooldown
- **AND** the account's latest primary-window snapshot is exhausted with a future reset
- **AND** the paid extra-usage opt-in does not admit it
- **WHEN** an Anthropic request is routed
- **THEN** the account is excluded from standard routing
