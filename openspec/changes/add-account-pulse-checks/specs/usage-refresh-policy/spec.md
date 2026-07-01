## ADDED Requirements

### Requirement: Scheduled account pulse checks keep stored account state truthful

The system SHALL run a periodic background pulse that verifies every stored,
non-paused account against its provider with a real minimal request, and SHALL
reconcile the account's stored state so operators can distinguish three states
without manual probing: usable (authenticated and subscribed), authenticated but
unsubscribed (subscription ledger `canceled`, credentials intact), and disconnected
(`reauth_required`). Classification MUST be conservative: only an explicit success
(2xx), an explicit credential rejection (401), or a 403 carrying a known
subscription-refusal marker may change account state; any other outcome MUST leave
state untouched and back off.

#### Scenario: Subscription lapse is detected on an idle account

- **GIVEN** an `active` account whose subscription ledger is not `canceled`
- **WHEN** a pulse probe returns 403 with "OAuth authentication is currently not allowed for this organization"
- **THEN** the subscription ledger is set to `canceled` with the upstream message in the notes
- **AND** the account `status` remains unchanged (credentials are intact)
- **AND** the account selection cache is invalidated

#### Scenario: A stale auth-failure status is corrected to unsubscribed

- **GIVEN** an account in `deactivated` or `reauth_required` status
- **WHEN** a pulse probe authenticates but returns a subscription-refusal 403
- **THEN** the account `status` becomes `active` (the stored auth-failure diagnosis was wrong)
- **AND** the subscription ledger is `canceled`, keeping the account out of the routable pool
- **AND** dashboards show it as authenticated-but-unsubscribed rather than needing re-auth

#### Scenario: Subscription recovery is detected automatically

- **GIVEN** an account whose subscription ledger is `canceled`
- **WHEN** a pulse probe returns a 2xx
- **THEN** the subscription ledger is set to `active` with a pulse note and a fresh `last_verified_at`
- **AND** the account re-enters the routable pool without operator action

#### Scenario: Credential rejection marks the account disconnected

- **GIVEN** an `active` account
- **WHEN** a pulse probe returns 401, or the pre-probe token refresh fails permanently
- **THEN** the account `status` becomes `reauth_required` with a pulse reason

#### Scenario: Recovered credentials reactivate the account

- **GIVEN** an account in `deactivated` or `reauth_required` status
- **WHEN** a pulse probe returns a 2xx
- **THEN** the account `status` becomes `active` and the deactivation reason is cleared

#### Scenario: Ambiguous probe outcomes never change state

- **WHEN** a pulse probe returns a network failure, a 400, a 403 without a known subscription-refusal marker, a 429, or a 5xx
- **THEN** no account state is written
- **AND** the account enters exponential failure backoff for subsequent pulse cycles

#### Scenario: Paused accounts are never pulsed

- **GIVEN** an account in `paused` status
- **WHEN** a pulse cycle runs
- **THEN** no probe is sent for that account

#### Scenario: Pulse is leader-gated and tunable

- **GIVEN** a deployment with more than one replica and leader election disabled
- **WHEN** the application starts
- **THEN** the pulse scheduler does not run
- **AND** operators can disable the pulse or tune its interval, concurrency, jitter, and backoff via `ACCOUNT_PULSE_*` environment settings
