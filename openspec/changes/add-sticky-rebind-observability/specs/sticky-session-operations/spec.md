## ADDED Requirements

### Requirement: Sticky-session rebinds are recorded with their cause

The system MUST record a durable rebind event whenever sticky selection resolves
an existing sticky mapping to a different account than the one previously stored.
The event MUST capture the sticky key in hashed form, the sticky kind, the old
account id, the new account id, the reason the rebind occurred, and a timestamp.
The reason MUST be one of `rate_limited_failover`, `budget_pressure`,
`burn_first_drain`, `account_unavailable`, or `other`, and MUST be taken from the
selection branch that actually forced the rebind rather than inferred afterward.

A selection that re-resolves an existing mapping to the same account MUST record
nothing, and creating a mapping where none previously existed MUST record
nothing — only a change of the pinned account for an existing mapping is a
rebind.

Recording MUST be fail-open: an error while persisting the event MUST NOT fail or
delay the proxied request beyond a single log line, and the selection result MUST
be identical whether or not the event is persisted.

#### Scenario: Rate-limit failover rebind is recorded

- **GIVEN** an existing durable pin whose account is rate-limited and cannot be
  served after the grace-period retry
- **WHEN** selection rebinds the mapping to a fallback account
- **THEN** a rebind event is recorded with reason `rate_limited_failover`, the
  old and new account ids, the sticky kind, and a hashed sticky key

#### Scenario: Budget-pressure rebind is recorded

- **GIVEN** an existing pin whose account is above the sticky reallocation budget
  threshold while a healthier account remains below it
- **WHEN** selection rebinds the mapping to the healthier account
- **THEN** a rebind event is recorded with reason `budget_pressure`

#### Scenario: Burn-first drain rebind is recorded

- **GIVEN** an existing under-threshold pin and an enabled fable burn-first sticky
  drain with a selectable burn-first account
- **WHEN** selection drains the mapping onto the burn-first account
- **THEN** a rebind event is recorded with reason `burn_first_drain`

#### Scenario: Unavailable-account rebind is recorded

- **GIVEN** an existing pin whose account is paused, deactivated, or no longer in
  the pool
- **WHEN** selection rebinds the mapping to a fallback account
- **THEN** a rebind event is recorded with reason `account_unavailable`

#### Scenario: Same-account touch records nothing

- **GIVEN** an existing pin whose account is re-selected onto the same account
- **WHEN** selection completes
- **THEN** no rebind event is recorded

#### Scenario: Recording failure does not break selection

- **GIVEN** a rebind that would be recorded
- **AND** persisting the rebind event fails
- **WHEN** selection completes
- **THEN** the same fallback account is returned and the mapping is still rebound
