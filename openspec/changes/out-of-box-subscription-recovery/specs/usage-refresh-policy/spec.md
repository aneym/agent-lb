## ADDED Requirements

### Requirement: Account pulse recovery lane detects upstream-fixed accounts within minutes

Between full pulse passes the system SHALL run a fast recovery lane every
`account_pulse_recovery_interval_seconds` (default 900, tunable via
`ACCOUNT_PULSE_RECOVERY_INTERVAL_SECONDS`) that probes only recovery-pending
accounts: accounts that are not `paused` and whose subscription ledger is
`canceled` or whose status is `reauth_required` or `deactivated`. Recovery
probes MUST use the same probe senders and verdict handling as full passes. A
recovery-pending account MUST NOT be probed by the recovery lane more often
than once per recovery interval, and failure backoff MUST NOT delay a
recovery-pending account's recovery-lane candidacy by more than one recovery
interval (detection MAY slip by one additional recovery-lane pass when a wake
coincides with a full pass). Full passes MUST keep running on `account_pulse_interval_seconds`
with unchanged candidate selection and backoff behavior.

#### Scenario: A resubscribed account re-enters the pool within one recovery cycle

- **GIVEN** an account whose subscription ledger is `canceled`
- **WHEN** the operator resubscribes the account upstream and a recovery-lane probe returns a 2xx
- **THEN** the subscription ledger is restored to `active`
- **AND** the account re-enters the routable pool without operator action within one recovery cycle (the recovery interval plus configured jitter)

#### Scenario: A re-authenticated account is reactivated within the recovery interval

- **GIVEN** an account in `reauth_required` or `deactivated` status
- **WHEN** a recovery-lane probe returns a 2xx
- **THEN** the account status becomes `active` and the deactivation reason is cleared

#### Scenario: Healthy accounts are not probed by the recovery lane

- **GIVEN** an `active` account whose subscription ledger is not `canceled`
- **WHEN** a recovery-lane pass runs
- **THEN** no probe is sent for that account
- **AND** the account is still probed by full passes

#### Scenario: A still-broken account is probed at most once per recovery interval

- **GIVEN** a recovery-pending account whose probes keep failing
- **WHEN** consecutive recovery-lane passes run
- **THEN** the account is probed at most once per recovery interval
- **AND** a still-canceled subscription refusal does not escalate failure backoff

#### Scenario: Failure backoff cannot push recovery detection past the full interval

- **GIVEN** a recovery-pending account whose exponential failure backoff exceeds the recovery interval
- **WHEN** the recovery interval has elapsed since the failure was recorded
- **THEN** the next recovery-lane pass probes the account

#### Scenario: Paused accounts are never probed by the recovery lane

- **GIVEN** a `paused` account with a `canceled` subscription ledger
- **WHEN** a recovery-lane pass runs
- **THEN** no probe is sent for that account

#### Scenario: A recovery interval at or above the full interval disables the fast lane

- **GIVEN** `account_pulse_recovery_interval_seconds` configured greater than or equal to `account_pulse_interval_seconds`
- **WHEN** the pulse scheduler runs
- **THEN** every wake is a full pass on the full-interval cadence
