## ADDED Requirements

### Requirement: Active reset markers are advisory rather than standalone blocks
The status CLI MUST NOT classify an active account as blocked solely because rateLimitResetAt is in the future. It MUST retain the raw marker in JSON and MUST continue to report blocked statuses, canceled subscriptions and observed exhausted primary or weekly windows with future resets as blocked. Missing or malformed usage MUST remain unknown.

#### Scenario: Recovered account retains an obsolete marker
- **GIVEN** an active account with observed primary and weekly headroom and a future rateLimitResetAt
- **WHEN** status observes the account
- **THEN** the account is usable and the raw marker remains visible

#### Scenario: Active label does not conceal exhaustion
- **GIVEN** an active account with a primary or weekly remaining percentage of zero and a future window reset
- **WHEN** status observes the account
- **THEN** the account remains blocked regardless of its account reset marker
