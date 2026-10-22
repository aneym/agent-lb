# usage-refresh-policy Specification

## Purpose
Define how background usage refresh reacts to auth-like failures without permanently hammering bad accounts.

Requirements continue in these capability specs:

- [usage-refresh-quota-capacity](../usage-refresh-quota-capacity/spec.md): plan types, credit-backed quota, primary-window priming and reset overrides

## Requirements

### Requirement: Usage refresh cools down repeated auth-like failures

Background usage refresh MUST apply a cooldown to accounts that repeatedly fail usage refresh with ambiguous `401` or `403` responses. Accounts in that cooldown window MUST be skipped until the cooldown expires or a later successful refresh clears it.

#### Scenario: Ambiguous usage 401 enters cooldown
- **WHEN** usage refresh receives a `401` that does not match a permanent deactivation signal
- **THEN** the account is not deactivated immediately
- **AND** subsequent refresh cycles skip the account until the cooldown window expires

#### Scenario: Successful refresh clears cooldown
- **WHEN** a later usage refresh succeeds for an account that had been cooled down
- **THEN** the cooldown is cleared
- **AND** normal refresh cadence resumes

### Requirement: Usage refresh deactivates on clear deactivation signals

The system MUST deactivate accounts when usage refresh receives a permanent deactivation signal. At minimum, `402`, `404`, and `401` responses whose message explicitly indicates that the OpenAI account has been deactivated MUST be treated as deactivation signals.

#### Scenario: Usage 401 deactivation message deactivates the account
- **WHEN** usage refresh receives HTTP `401`
- **AND** the upstream message states that the OpenAI account has been deactivated
- **THEN** the account is marked `deactivated`
- **AND** later usage refresh cycles skip that account

### Requirement: token_expired at the refresh boundary deactivates the account

When the OAuth refresh endpoint fails with error code `token_expired`, the system MUST treat it as a permanent authentication failure on par with `refresh_token_expired` / `refresh_token_reused` / `refresh_token_invalidated`. The affected account MUST be deactivated and removed from the routing pool until it is re-authenticated.

#### Scenario: Refresh-time `token_expired` is classified as permanent

- **WHEN** `classify_refresh_error("token_expired")` is evaluated
- **THEN** it returns `True`

#### Scenario: Refresh-time `token_expired` deactivates the account

- **WHEN** `AuthManager.refresh_account` receives a `RefreshError("token_expired", ..., is_permanent=True)` from `refresh_access_token`
- **THEN** the account is transitioned to `DEACTIVATED`
- **AND** the deactivation reason references the re-login requirement so the dashboard can surface it
- **AND** the account is no longer selected by the load balancer until it is re-authenticated

#### Scenario: Usage-refresh-time `token_expired` deactivates the account

- **WHEN** background usage refresh observes an upstream error whose code is `token_expired` (via `_should_deactivate_for_usage_error`'s permanent-code check)
- **THEN** the account is transitioned to `DEACTIVATED` immediately, without entering the ambiguous-401 cooldown loop

### Requirement: Background usage refresh reconciles recoverable blocked statuses
Background usage refresh SHALL reconcile persisted `rate_limited` and `quota_exceeded` accounts back to `active` after it writes fresh usage snapshots that prove the blocked window has recovered. This reconciliation SHALL be recovery-only and SHALL NOT promote `active` accounts into blocked statuses.

#### Scenario: Scheduler recovers a stale rate-limited account from fresh primary usage
- **WHEN** an account is persisted as `rate_limited`
- **AND** the persisted rate-limit reset deadline has already elapsed
- **AND** a later background usage refresh writes a fresh primary usage row recorded after the persisted block marker
- **AND** that primary usage row reports usage below `100%`
- **THEN** the scheduler marks the account `active`
- **AND** it clears persisted `reset_at` and `blocked_at`

#### Scenario: Scheduler recovers a legacy rate-limited account without a block marker
- **WHEN** an account is persisted as `rate_limited`
- **AND** the persisted rate-limit reset deadline has already elapsed
- **AND** the account has no persisted block marker
- **AND** a later background usage refresh writes a recent primary usage row that reports usage below `100%`
- **THEN** the scheduler marks the account `active`
- **AND** it clears persisted `reset_at`

#### Scenario: Scheduler preserves legacy rate-limited accounts without recent primary usage
- **WHEN** an account is persisted as `rate_limited`
- **AND** the persisted rate-limit reset deadline has already elapsed
- **AND** the account has no persisted block marker
- **AND** the latest primary usage row is not recent enough to prove background refresh recovery
- **THEN** the scheduler leaves the account `rate_limited`

#### Scenario: Scheduler preserves an unexpired rate-limit cooldown
- **WHEN** an account is persisted as `rate_limited`
- **AND** its persisted rate-limit reset deadline is still in the future
- **AND** a later background usage refresh writes a fresh primary usage row recorded after the persisted block marker
- **AND** that primary usage row reports usage below `100%`
- **THEN** the scheduler leaves the account `rate_limited`

#### Scenario: Scheduler recovers a stale quota-exceeded account from fresh secondary usage
- **WHEN** an account is persisted as `quota_exceeded`
- **AND** a later background usage refresh writes a fresh secondary usage row that reports usage below `100%`
- **THEN** the scheduler marks the account `active`
- **AND** it clears persisted `reset_at` and `blocked_at`

#### Scenario: Scheduler does not tighten active accounts into blocked statuses
- **WHEN** background usage refresh evaluates an account currently persisted as `active`
- **THEN** the scheduler does not change that account to `rate_limited` or `quota_exceeded`

#### Scenario: Scheduler ignores stale pre-block recovery evidence
- **WHEN** an account is persisted as `rate_limited`
- **AND** the latest primary usage row was recorded before the persisted block marker
- **THEN** the scheduler leaves the account blocked

#### Scenario: Scheduler skips recovery when the account row changed concurrently
- **WHEN** background usage refresh determines that a blocked account is recoverable
- **AND** the persisted account status or reset markers change before the scheduler writes recovery
- **THEN** the scheduler skips the stale recovery write

#### Scenario: Scheduler clears stale deactivation reasons on recovery
- **WHEN** background usage refresh recovers a `rate_limited` or `quota_exceeded` account to `active`
- **THEN** the scheduler writes `deactivation_reason` as `NULL`

### Requirement: Usage refresh does not trust elapsed reset windows

Background usage refresh MUST treat a latest usage row as stale when that row's `reset_at` timestamp is in the past, even when the row's `recorded_at` timestamp is still within the normal refresh interval.

#### Scenario: Past reset_at bypasses freshness

- **GIVEN** the latest usage row was recorded within the normal refresh interval
- **AND** that row's `reset_at` timestamp has already elapsed
- **WHEN** background usage refresh evaluates the account
- **THEN** the row is treated as stale
- **AND** agent-lb attempts a fresh upstream usage fetch

### Requirement: Blocked accounts refresh once their reset deadline elapses

When an account is `RATE_LIMITED` or `QUOTA_EXCEEDED` and its persisted `reset_at` timestamp has elapsed, background usage refresh MUST bypass the normal freshness interval so the account can recover from the upstream post-reset state. The bypass MUST NOT apply before the persisted reset deadline elapses.

#### Scenario: Quota-exceeded account with fresh primary row reaches reset deadline

- **GIVEN** an account is marked `QUOTA_EXCEEDED`
- **AND** the account's persisted `reset_at` timestamp has elapsed
- **AND** the latest primary usage row is still within the normal refresh interval
- **WHEN** background usage refresh evaluates the account
- **THEN** agent-lb performs an upstream usage fetch instead of waiting for the primary row to age out

#### Scenario: Rate-limited account reaches reset deadline

- **GIVEN** an account is marked `RATE_LIMITED`
- **AND** the account's persisted `reset_at` timestamp has elapsed
- **WHEN** background usage refresh evaluates the account
- **THEN** agent-lb performs an upstream usage fetch instead of waiting for the normal refresh interval

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

### Requirement: Reset consumption has durable recovery
The service MUST persist one active logical reset attempt with its account, credit, trigger and stable redemption request ID before calling upstream. An uncertain result MUST retain that attempt and MUST NOT cause consumption of another credit. The service MUST reconcile inventory and usage before retrying an uncertain attempt, MUST reuse its request ID, and MUST persist confirmed consumption before usage refresh. An applied attempt MUST block new consumption until fresh usage proves recovery. Successful exhaustion cooldowns MUST survive restart.

#### Scenario: Upstream timeout after consumption
- **WHEN** upstream consumption has an unknown result
- **THEN** another account or credit MUST NOT be consumed
- **AND** a later recovery attempt MUST use the original account, credit and request ID.

#### Scenario: Refresh fails after confirmed consumption
- **WHEN** upstream confirms reset and usage refresh fails
- **THEN** the durable attempt MUST remain applied
- **AND** recovery MUST refresh or probe without consuming another credit.

#### Scenario: Concurrent or restarted workers
- **WHEN** redemption requests overlap or the service restarts
- **THEN** only one active logical redemption MAY exist
- **AND** the existing attempt and persisted cooldown MUST govern subsequent work.

### Requirement: Exhaustion spending uses fresh standard usage
Before an exhaustion-triggered redemption, the scheduler MUST refresh each subscription-usable serving OpenAI account and re-evaluate standard quota availability. Unknown or failed refresh MUST suppress new consumption. Healthy capacity MUST suppress exhaustion redemption. Additional model quota exhaustion alone MUST NOT trigger a standard reset. Paused, disconnected and subscription-unusable accounts MUST remain excluded. Zero available credits MUST cause no consumption.

#### Scenario: Persisted exhaustion is stale
- **WHEN** fresh usage shows an available standard window on any serving account
- **THEN** no exhaustion credit is consumed and ordinary routing can use recovered capacity.

#### Scenario: Full exhaustion and a saved credit
- **WHEN** fresh standard usage confirms every serving OpenAI account is exhausted and a saved credit is available
- **THEN** the scheduler MAY redeem one credit through the durable recovery flow.

### Requirement: Expiry policy and manual contract remain compatible
Expiring-credit redemption MUST retain its separate expiry eligibility policy and manual API responses MUST retain their existing schema. Both paths MUST respect the same active redemption attempt so neither creates duplicate or competing consumption while an outcome remains uncertain.

#### Scenario: Expiry overlaps an uncertain exhaustion reset
- **WHEN** an exhaustion reset remains pending
- **THEN** the expiry sweep MUST reconcile it before creating another redemption.
