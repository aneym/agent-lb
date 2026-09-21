# Spec Delta

## Purpose

Define privacy-safe, fail-closed capacity telemetry used to admit Anthropic coding-agent seats.

## ADDED Requirements

### Requirement: Limit-watch emits verified provider capacity
The capacity poller SHALL count an active Anthropic account as usable only when its primary and secondary remaining percentages are numeric and strictly above the configured reserve. It SHALL count an active OpenAI account with a null primary percentage as usable when its numeric secondary weekly percentage is strictly above reserve; this exception exists because OpenAI legitimately omits that primary window. A refresh timestamp older than fifteen minutes SHALL make an account unusable. An absent refresh timestamp SHALL remain usable when all other admission requirements pass and SHALL be recorded as `freshness: unknown`.

#### Scenario: Anthropic has an unknown core window
- **WHEN** an active Anthropic account reports a missing or null core remaining percentage
- **THEN** the provider usable count excludes that account
- **AND** its snapshot reason names the unknown window without exposing an email

#### Scenario: OpenAI reports no primary window
- **WHEN** an active OpenAI account reports a null primary percentage and a numeric weekly percentage above reserve
- **THEN** the provider usable count includes that account

#### Scenario: Account telemetry is stale
- **WHEN** an account refresh timestamp is older than fifteen minutes
- **THEN** the provider usable count excludes that account
- **AND** its snapshot reason reports stale freshness

### Requirement: Limit-watch snapshot reports safe admission diagnostics
The reachable snapshot SHALL retain `polled_at`, `reachable`, `providers.<provider>.usable_count`, and `fable_eligible_usable`. For each provider it SHALL include the minimum remaining percentage across usable accounts and an entry for each unusable account containing only the first eight characters of its account identifier and one or more reason strings. The Fable eligible usable count SHALL include only generally usable Fable-eligible accounts whose `anthropic_fable_scoped_weekly` remaining percentage is numeric and strictly above reserve.

#### Scenario: Fable scoped weekly quota is at reserve
- **WHEN** a generally usable Fable-eligible Anthropic account has scoped weekly remaining percentage equal to reserve
- **THEN** it is excluded from `fable_eligible_usable`

#### Scenario: Snapshot has usable and unusable accounts
- **WHEN** a provider has accounts in both states
- **THEN** its snapshot includes the smallest core remaining percentage of its usable accounts
- **AND** a reason entry for each unusable account with a truncated identifier
