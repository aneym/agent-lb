## MODIFIED Requirements

### Requirement: Reset-confirmed limit warm-up

The system SHALL support an optional limit warm-up mechanism whose global toggle is disabled by default. Newly created accounts SHALL default to per-account warm-up opt-in enabled; existing accounts keep their stored opt-in state. When enabled globally and for an account, background usage refresh MAY send one minimal upstream Responses request after it confirms that a selected quota window has moved from an exhausted sample to a newly available reset window.

#### Scenario: Warm-up is skipped unless reset is confirmed

- **GIVEN** limit warm-up is enabled globally and for an account
- **AND** the account's previous usage sample for a selected window was exhausted
- **WHEN** background usage refresh records a newer sample for that window with `used_percent < 100` and a later `reset_at`
- **THEN** the system sends at most one warm-up request for that account/window/reset tuple

#### Scenario: Warm-up is globally gated and safe by default

- **GIVEN** background usage refresh is preparing to evaluate limit warm-up candidates
- **WHEN** global limit warm-up is disabled
- **OR** the account is opted out
- **THEN** background usage refresh MUST NOT send warm-up traffic

#### Scenario: New accounts default to warm-up opt-in

- **WHEN** a new account is persisted without an explicit per-account warm-up opt-in value
- **THEN** the stored account has limit warm-up enabled
- **AND** warm-up traffic for it still requires the global toggle to be enabled

#### Scenario: Warm-up uses fresh opt-in state after usage refresh

- **GIVEN** an account was loaded before a background usage refresh cycle
- **AND** the account's limit warm-up opt-in changes while the refresh cycle is running
- **WHEN** the scheduler evaluates warm-up candidates after writing usage samples
- **THEN** the scheduler MUST evaluate the latest persisted opt-in value rather than the stale in-session account object

#### Scenario: Warm-up respects unsafe account states

- **WHEN** an account is paused, deactivated, rate-limited, quota-exceeded, in an auth-refresh failure path, or its subscription ledger status is `canceled`
- **THEN** limit warm-up MUST NOT send traffic for that account

#### Scenario: Warm-up targets only subscription-usable accounts

- **GIVEN** an account whose subscription ledger status is `canceled`
- **AND** the account `status` is still `active`
- **WHEN** the scheduler evaluates warm-up candidates after a usage refresh
- **THEN** the account is not a warm-up candidate
- **AND** no warm-up attempt row or request log is persisted for it

#### Scenario: Warm-up attempts are durable and deduplicated

- **WHEN** multiple refresh workers observe the same account/window/reset candidate
- **THEN** the database permits at most one persisted attempt for that tuple
- **AND** later refresh cycles skip that tuple after a prior attempt exists
