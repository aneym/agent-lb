## ADDED Requirements

### Requirement: Fable-class routing respects weekly headroom

For Anthropic accounts, the balancer SHALL treat requests for Fable-class models
(model id containing `fable`, case-insensitive) differently from other Anthropic
traffic, gated on each account's weekly (secondary) window usage against a
configurable threshold (default 50%, `ANTHROPIC_FABLE_WEEKLY_MAX_USED_PERCENT`;
feature toggle `ANTHROPIC_FABLE_ROUTING_ENABLED`). The stamp used for the inverse
preference MUST be request-scoped: it MUST NOT persist to the account row and MUST
NOT leak through the selection-inputs cache.

#### Scenario: Fable requests route only to accounts with weekly headroom

- **GIVEN** Anthropic accounts where some have weekly usage at or above the threshold
- **WHEN** a request for a Fable-class model is routed
- **THEN** only accounts under the threshold are candidates
- **AND** among them the existing strategy ordering applies unchanged

#### Scenario: Fable never hard-fails purely on the local threshold

- **GIVEN** every cooldown-eligible Anthropic account is at or above the threshold
- **WHEN** a Fable-class request is routed
- **THEN** the balancer falls back to the unfiltered cooldown-eligible pool
- **AND** logs a warning that Fable headroom is exhausted locally

#### Scenario: Non-Fable traffic drains over-threshold accounts first

- **GIVEN** Anthropic accounts on both sides of the threshold, all with stored routing policy `normal`
- **WHEN** a non-Fable Anthropic request is routed
- **THEN** accounts at/over the threshold are treated as `burn_first` for that selection and are drained before under-threshold accounts

#### Scenario: Operator routing policies are not overridden

- **GIVEN** an over-threshold account whose stored routing policy is `preserve`
- **WHEN** a non-Fable Anthropic request is routed
- **THEN** the account keeps `preserve` semantics and is not stamped `burn_first`

#### Scenario: Eligibility is dashboard-visible

- **WHEN** `/api/accounts` renders an Anthropic account
- **THEN** the row carries `fableEligible` reflecting weekly usage below the threshold
- **AND** non-Anthropic rows carry `fableEligible: null`

#### Scenario: Feature can be disabled

- **GIVEN** `ANTHROPIC_FABLE_ROUTING_ENABLED=false`
- **WHEN** any Anthropic request is routed
- **THEN** selection behaves exactly as before this change
