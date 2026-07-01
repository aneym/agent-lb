## ADDED Requirements

### Requirement: Canceled subscription accounts
Canceled subscription accounts SHALL not be routable. When an account's local
subscription ledger status is `canceled`, the load balancer SHALL exclude that
account from account selection even if its account auth status is otherwise
`active`, `rate_limited`, or `quota_exceeded`.

#### Scenario: Canceled subscription is excluded from account selection

- **GIVEN** an account has status `active`
- **AND** its local subscription ledger status is `canceled`
- **WHEN** account selection builds candidates for routing
- **THEN** the account is not eligible for selection

#### Scenario: Non-canceled subscription metadata remains routable while it works

- **GIVEN** an account has status `active`
- **AND** its local subscription ledger status is `paused`
- **WHEN** account selection builds candidates for routing
- **THEN** the account may remain eligible subject to existing routing, quota,
  and health gates

## MODIFIED Requirements

### Requirement: Relative availability routing

The proxy account selector SHALL support a `relative_availability` routing
strategy. The strategy SHALL evaluate only accounts that have passed the
existing eligibility, health-tier, model-plan, quota, cooldown,
subscription-usability, circuit-breaker, and budget-safety gates. For each
candidate, it SHALL compute a raw score from remaining secondary-window credits
divided by seconds until the secondary-window reset, using bounded fallbacks for
unknown or near-immediate reset times, and SHALL select from the highest
weighted candidates according to the configured power and top-K cutoff.

#### Scenario: Relative availability preserves canonical gates

- **GIVEN** one account is paused, deactivated, rate-limited, quota-exceeded,
  cooling down, outside the requested model plan, or locally subscription
  `canceled`
- **WHEN** account selection uses `relative_availability`
- **THEN** that account is not selected by the relative-availability strategy
