## MODIFIED Requirements

### Requirement: Reset-confirmed limit warm-up

The system SHALL support an optional OpenAI limit warm-up mechanism that is disabled by default. When enabled globally and for an account, background usage refresh MAY send one minimal upstream Responses request after it confirms that a selected quota window has moved from an exhausted sample to a newly available reset window. Anthropic primary-window priming is governed by the separate continuous-priming requirement below.

#### Scenario: Warm-up is skipped unless reset is confirmed
- **GIVEN** OpenAI limit warm-up is enabled globally and for an account
- **AND** the account's previous usage sample for a selected window was exhausted
- **WHEN** background usage refresh records a newer sample for that window with `used_percent < 100` and a later `reset_at`
- **THEN** the system sends at most one warm-up request for that account/window/reset tuple

#### Scenario: Warm-up is opt-in and safe by default
- **GIVEN** background usage refresh is preparing to evaluate OpenAI limit warm-up candidates
- **WHEN** global limit warm-up is disabled
- **OR** the account is not opted in
- **THEN** background usage refresh MUST NOT send OpenAI warm-up traffic

#### Scenario: Warm-up uses fresh opt-in state after usage refresh
- **GIVEN** an account was loaded before a background usage refresh cycle
- **AND** the account's limit warm-up opt-in changes while the refresh cycle is running
- **WHEN** the scheduler evaluates warm-up candidates after writing usage samples
- **THEN** the scheduler MUST evaluate the latest persisted opt-in value rather than the stale in-session account object

#### Scenario: Warm-up respects unsafe account states
- **WHEN** an account is paused, deactivated, rate-limited, quota-exceeded, or in an auth-refresh failure path
- **THEN** limit warm-up MUST NOT send traffic for that account

#### Scenario: Warm-up attempts are durable and deduplicated
- **WHEN** multiple refresh workers observe the same account/window/reset candidate
- **THEN** the database permits at most one persisted attempt for that tuple
- **AND** later refresh cycles skip that tuple after a prior attempt exists

## ADDED Requirements

### Requirement: Continuous Anthropic primary-window priming

The scheduler MUST evaluate each known Anthropic account primary reset shortly after its timestamp, regardless of the legacy warmup working-hours band, global OpenAI warmup setting, or the previous window's used percent. It MUST send at most one successful one-token Haiku Messages primer for an account and reset, using the existing per-account warmup opt-out and durable attempts table.

#### Scenario: A partially used five-hour window resets

- **GIVEN** an active, opted-in Anthropic account with a known primary reset and a nonexhausted weekly window
- **WHEN** the reset time passes and fresh usage shows free primary, weekly, and Haiku quota with no already-opened next primary window
- **THEN** the scheduler sends one minimal primer for that account and reset
- **AND** a subsequent usage refresh confirms that the primary reset moved approximately five hours forward
- **AND** later scheduler passes and process restarts MUST NOT send a duplicate for that reset

#### Scenario: A send explicitly fails

- **WHEN** an Anthropic primer returns an upstream error or transport failure
- **THEN** the failed attempt is retained and retried only after backoff, with one in-flight claimant

#### Scenario: Priming would risk overage

- **WHEN** the account is disabled, quota-exceeded, weekly-capped, unsubscribed, or fresh primary, weekly, or Haiku free-quota evidence is missing
- **THEN** the scheduler MUST NOT send a primer or enter the extra-usage credit-billing path

#### Scenario: Operator observes primer state

- **WHEN** a primer is attempted and later confirmed
- **THEN** the service logs the attempt and result without credentials
- **AND** account status exposes the last confirmed prime time
