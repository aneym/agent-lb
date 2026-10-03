## ADDED Requirements

### Requirement: Subscription routing refuses paid Anthropic extra usage
When `anthropic_route_to_extra_usage` is false, agent-lb MUST NOT serve `/v1/messages` from an account whose latest secondary snapshot is at or above 100 percent with a future reset or an unknown reset. That account's reset MUST be included in the pool-exhausted retry time when the reset is known. Token counting MUST stay exempt. An active extra-usage tripwire MUST remain an exclusion until both a primary snapshot and a secondary snapshot recorded after the tripwire are under 100 percent. An account with no secondary snapshot MUST lift the tripwire on a newer primary snapshot under 100 percent, and the tripwire's own reset MUST still end it. A 2xx response whose headers report extra-usage billing MUST NOT be forwarded: agent-lb MUST record the existing extra-usage cooldown, close the upstream response without reading the body, persist a request log with status `error` and error code `extra_usage_refused`, and continue selection. When no account remains, the client MUST receive the existing pool-exhausted usage-limit error and its reset time. An account selected as a paid fallback MUST keep the previous behavior of streaming that response.

#### Scenario: Weekly exhaustion blocks a recovered five-hour window
- **GIVEN** extra-usage routing is off
- **AND** an account's latest secondary snapshot is at or above 100 percent with a future reset
- **AND** a newer primary snapshot is under 100 percent and was recorded after an extra-usage tripwire
- **AND** another account has subscription headroom
- **WHEN** a `/v1/messages` request is routed
- **THEN** agent-lb selects the account with subscription headroom
- **AND** the weekly-exhausted account is not called

#### Scenario: Weekly exhaustion alone returns the weekly reset
- **GIVEN** extra-usage routing is off
- **AND** the only account has a secondary snapshot at or above 100 percent with a future reset
- **WHEN** a `/v1/messages` request is routed
- **THEN** the client receives the usage-limit error carrying that weekly reset

#### Scenario: Overage success fails over without the billed body
- **GIVEN** extra-usage routing is off
- **AND** account A answers 200 with extra-usage billing headers
- **AND** account B answers 200 without those headers
- **WHEN** a `/v1/messages` request is routed to A first
- **THEN** the client receives B's body
- **AND** A's request log is an error with code `extra_usage_refused`
- **AND** A has an extra-usage cooldown

#### Scenario: Overage success with no alternate account is a usage limit
- **GIVEN** extra-usage routing is off
- **AND** the only account answers 200 with extra-usage billing headers
- **WHEN** a `/v1/messages` request is routed
- **THEN** the client receives the usage-limit error
- **AND** the billed body is not forwarded

#### Scenario: Paid fallback still streams the billed response
- **GIVEN** extra-usage routing is on
- **AND** the selected account is a paid fallback
- **WHEN** that account answers 200 with extra-usage billing headers
- **THEN** the client receives that response body
