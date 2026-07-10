## ADDED Requirements

### Requirement: Chronic refresh identity mismatches escalate visibly

When consecutive usage-refresh cycles for an account are discarded because the payload identity (plan type or workspace) contradicts the stored slot, the service MUST escalate at the third consecutive discarded cycle by emitting exactly one ERROR-level log for the streak and exposing an `identityMismatch` object (mismatch count, first and last mismatch times, stored and payload plan types) on the account's API summary. An accepted refresh MUST clear the streak and remove the `identityMismatch` field.

#### Scenario: lapsed plan surfaces after three discarded cycles

- **GIVEN** an account whose refresh payload plan no longer matches its stored plan
- **WHEN** the third consecutive refresh cycle is discarded for that identity mismatch
- **THEN** one ERROR-level log records the escalation with the stored and payload identities
- **AND** the accounts API summary for that account includes `identityMismatch` with the streak count and timestamps

#### Scenario: accepted refresh clears the escalation

- **GIVEN** an account with an escalated identity-mismatch streak
- **WHEN** a subsequent refresh payload matches the stored identity and is accepted
- **THEN** the streak resets and the accounts API summary no longer includes `identityMismatch`

#### Scenario: below the escalation threshold

- **GIVEN** an account with fewer than three consecutive discarded mismatch cycles
- **WHEN** the accounts API summary is built
- **THEN** the summary omits `identityMismatch`
