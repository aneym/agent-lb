## ADDED Requirements

### Requirement: Banked reset credits are auto-redeemed only on full pool exhaustion

The service MUST run a leader-elected background scheduler that automatically
redeems at most one banked rate-limit reset credit per cooldown window, and
only when no subscription-usable OpenAI account in a serving status remains
`active`.

#### Scenario: Full exhaustion triggers exactly one redemption

- **WHEN** every subscription-usable OpenAI account with status in `active`, `rate_limited`, `quota_exceeded` has status `rate_limited` or `quota_exceeded`
- **AND** at least one such account has an available banked reset credit
- **AND** no auto-redemption occurred within the configured cooldown
- **THEN** the scheduler redeems exactly one credit — preferring `quota_exceeded` accounts, then the earliest-expiring available credit
- **AND** the redemption path refreshes the account's usage snapshot and invalidates the selection cache
- **AND** the redemption is audit-logged as `account_reset_credit_redeemed` with `trigger: auto`

#### Scenario: Any active account suppresses redemption

- **WHEN** at least one subscription-usable OpenAI account has status `active`
- **THEN** the scheduler redeems nothing, regardless of how many other accounts are exhausted or how many credits are banked

#### Scenario: Cooldown bounds credit spend

- **WHEN** an auto-redemption succeeded less than `reset_credit_auto_redeem_cooldown_seconds` ago
- **THEN** the scheduler redeems nothing on subsequent ticks until the cooldown elapses

#### Scenario: Non-serving accounts neither block nor trigger redemption

- **WHEN** the pool contains accounts that are `paused`, `deactivated`, `reauth_required`, or subscription-canceled
- **THEN** those accounts are excluded from the exhaustion check and from redemption candidates

#### Scenario: Candidate failure falls through

- **WHEN** redeeming on the preferred candidate fails (upstream or credential error)
- **THEN** the scheduler tries the next candidate in preference order within the same tick
- **AND** a tick with no successful redemption does not start the cooldown

#### Scenario: Kill switch

- **WHEN** `reset_credit_auto_redeem_enabled` is `false`
- **THEN** the scheduler performs no pool evaluation and no redemption
