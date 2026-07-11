# account-routing Specification

## Purpose
Define account selection and routing behavior for proxy traffic, including
relative-availability scoring, dashboard tuning, and privacy-preserving routing
diagnostics.

## Requirements
### Requirement: Relative availability routing
The proxy account selector SHALL support a `relative_availability` routing strategy. The strategy SHALL evaluate only accounts that have passed the existing eligibility, health-tier, model-plan, quota, cooldown, circuit-breaker, and budget-safety gates. For each candidate, it SHALL compute a raw score from remaining secondary-window credits divided by seconds until the secondary-window reset, using bounded fallbacks for unknown or near-immediate reset times, and SHALL select from the highest weighted candidates according to the configured power and top-K cutoff.

#### Scenario: Soon-resetting usable credits are preferred
- **GIVEN** two healthy eligible accounts with equal remaining secondary credits
- **AND** one account's secondary window resets sooner
- **WHEN** account selection uses `relative_availability`
- **THEN** the sooner-resetting account receives the higher relative-availability score

#### Scenario: Relative availability preserves canonical gates
- **GIVEN** one account is paused, deactivated, rate-limited, quota-exceeded, cooling down, or outside the requested model plan
- **WHEN** account selection uses `relative_availability`
- **THEN** that account is not selected by the relative-availability strategy

### Requirement: Relative availability dashboard tuning
Dashboard settings SHALL expose `relative_availability_power` and `relative_availability_top_k` alongside the routing strategy. The backend SHALL validate power as positive and top-K as an integer from 1 through 20. The dashboard UI SHALL reject non-integer top-K input without truncating decimal values.

#### Scenario: Sticky fallback uses configured tuning
- **GIVEN** a sticky request has no usable pinned account
- **AND** relative-availability routing is enabled with non-default power or top-K settings
- **WHEN** the load balancer falls back to fresh selection
- **THEN** it applies the configured relative-availability power and top-K values

#### Scenario: Decimal top-K input is rejected
- **WHEN** an operator enters `1.5` for relative availability top-K
- **THEN** the dashboard does not enable saving that value as `1`

### Requirement: Relative availability logs avoid raw account emails
Relative-availability selection diagnostics SHALL identify accounts using stable internal account IDs or another non-PII identifier. They SHALL NOT emit raw account emails in candidate, top-K, winner, or hot-path selected-account logs.

#### Scenario: Candidate logs use account IDs
- **WHEN** relative-availability routing logs candidate or winner diagnostics
- **THEN** the log message includes the candidate account ID
- **AND** the log message does not include the account email address

### Requirement: Anthropic model cooldowns recover from stale quota signals
The account pulse SHALL reconcile active `anthropic_top` and
`anthropic_top_thinking` cooldowns using probes that reproduce the corresponding
Fable request shape. A successful probe SHALL clear only the matching cooldown.
Network failures and non-success HTTP responses SHALL preserve it. Clearing
SHALL be conditional on the observed cooldown still being the latest state so a
newer concurrent rate-limit signal cannot be hidden.

#### Scenario: Standard top-model cooldown is disproved
- **GIVEN** an Anthropic account has an active `anthropic_top` cooldown
- **WHEN** its non-thinking Fable probe succeeds
- **THEN** the pulse clears only `anthropic_top`

#### Scenario: Thinking cooldown is disproved
- **GIVEN** an Anthropic account has an active `anthropic_top_thinking` cooldown
- **WHEN** its adaptive-thinking Fable probe succeeds
- **THEN** the pulse clears only `anthropic_top_thinking`

#### Scenario: Probe does not establish availability
- **GIVEN** an active Fable model cooldown
- **WHEN** its matching probe returns a network failure or non-success status
- **THEN** the pulse preserves the cooldown

#### Scenario: A newer cooldown wins the race
- **GIVEN** the pulse observed an active cooldown and started its probe
- **AND** a newer cooldown is stored before that probe succeeds
- **WHEN** the older probe attempts to clear its observed state
- **THEN** the compare-and-append operation makes no change
- **AND** the newer cooldown remains active
