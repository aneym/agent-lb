# Spec Delta

## Purpose

Define privacy-safe, fail-closed capacity telemetry used to admit Anthropic coding-agent seats.

## ADDED Requirements

### Requirement: Limit-watch emits verified provider capacity
The capacity poller SHALL count an active Anthropic account as usable only when its primary and secondary remaining percentages are numeric and strictly above the configured reserve. It SHALL count an active OpenAI account with a null primary percentage as usable when its numeric secondary weekly percentage is strictly above reserve; this exception exists because OpenAI legitimately omits that primary window. It SHALL exclude an account whose status is not `active` or whose `rateLimitResetAt` is in the future. It SHALL NOT use `lastRefreshAt` for usage freshness or usability, and SHALL record `freshness: unknown` for every non-Fable account.

#### Scenario: Anthropic has an unknown core window
- **WHEN** an active Anthropic account reports a missing or null core remaining percentage
- **THEN** the provider usable count excludes that account
- **AND** its snapshot reason names the unknown window without exposing an email

#### Scenario: OpenAI reports no primary window
- **WHEN** an active OpenAI account reports a null primary percentage and a numeric weekly percentage above reserve
- **THEN** the provider usable count includes that account

#### Scenario: OpenAI token refresh is old
- **WHEN** an otherwise healthy OpenAI account has a days-old `lastRefreshAt`
- **THEN** the provider usable count includes that account
- **AND** the snapshot records `freshness: unknown`

### Requirement: Limit-watch snapshot reports safe admission diagnostics
The reachable snapshot SHALL retain `polled_at`, `reachable`, `providers.<provider>.usable_count`, and `fable_eligible_usable`. For each provider it SHALL include the minimum remaining percentage across usable accounts and an entry for each unusable account containing only the first eight characters of its account identifier and one or more reason strings. The Fable eligible usable count SHALL include only generally usable Fable-eligible accounts whose `anthropic_fable_scoped_weekly` remaining percentage is numeric and strictly above reserve. When an account has `fableScopedWeekly`, the Fable count SHALL exclude it with reason `fable window stale` when `fresh` is `false` or its ISO `recordedAt` is older than fifteen minutes; an absent scoped freshness signal SHALL remain unknown and not itself exclude the account.

#### Scenario: Fable scoped weekly quota is at reserve
- **WHEN** a generally usable Fable-eligible Anthropic account has scoped weekly remaining percentage equal to reserve
- **THEN** it is excluded from `fable_eligible_usable`

#### Scenario: Snapshot has usable and unusable accounts
- **WHEN** a provider has accounts in both states
- **THEN** its snapshot includes the smallest core remaining percentage of its usable accounts
- **AND** a reason entry for each unusable account with a truncated identifier
