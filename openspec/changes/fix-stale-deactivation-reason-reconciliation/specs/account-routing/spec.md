## ADDED Requirements

### Requirement: Pulse clears stale deactivation reasons on healthy active accounts

When the account pulse probe returns a HEALTHY verdict for an account whose
status is `active` but whose stored `deactivation_reason` is non-null, the pulse
MUST clear the stored reason (keeping the account `active`) and emit an
`account_pulse_cleared_stale_deactivation_reason` audit action naming the
account and the cleared reason. Clients interpret a non-null
`deactivationReason` as a disconnected account, so a healthy probe proves the
stored reason is stale.

#### Scenario: Healthy probe on an active account with a leftover reason

- **GIVEN** an `active` account whose stored `deactivation_reason` is
  "Authentication failed: invalid_api_key"
- **WHEN** the account pulse probe returns HTTP 200
- **THEN** the account's `deactivation_reason` is cleared while the status stays
  `active`
- **AND** an `account_pulse_cleared_stale_deactivation_reason` audit action is
  recorded

#### Scenario: Healthy probe on a clean active account writes nothing

- **GIVEN** an `active` account with no stored `deactivation_reason`
- **WHEN** the account pulse probe returns HTTP 200
- **THEN** no account status write occurs
