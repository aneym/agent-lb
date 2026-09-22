## MODIFIED Requirements

### Requirement: All accounts default to remaining usage order

The macOS menu-bar All view MUST default to ascending remaining usage percentage,
independently of the provider-scoped sort preference. The comparison MUST use
weekly (secondary) remaining percentage exclusively, without monthly or
five-hour fallbacks. Unknown weekly values MUST sort last and known zero MUST remain
zero. Equal values MUST sort by case-insensitive display name then account ID.
Explicit alternative sort selections MUST remain available and persist separately
for All and provider-scoped views.

#### Scenario: Mixed providers are ordered by remaining weekly allowance

- **GIVEN** accounts from different providers have weekly remaining percentages of 40, 0 and 9
- **WHEN** the All view opens with no saved All-view sort preference
- **THEN** their order is 0, 9, 40 regardless of reset times or the provider-view sort preference

#### Scenario: Unknown usage is not treated as exhausted

- **GIVEN** one account has no known remaining percentage and another has zero weekly remaining
- **WHEN** sorting by remaining usage
- **THEN** the zero-remaining account precedes the unknown account

#### Scenario: Weekly telemetry takes precedence over shorter windows

- **GIVEN** an account has 10 percent weekly remaining and 100 percent five-hour remaining
- **WHEN** sorting by remaining usage
- **THEN** its comparison value is 10 percent
- **AND** an account without weekly telemetry sorts last even if monthly or five-hour telemetry is available

#### Scenario: Provider sort preference survives All-view selection

- **GIVEN** a provider-scoped view uses reset-soonest ordering
- **WHEN** the operator changes the All-view sort selection
- **THEN** the provider-scoped view keeps reset-soonest ordering
