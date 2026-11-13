## ADDED Requirements

### Requirement: Active selection does not resurrect account reset markers
Selection state for a persisted ACTIVE account MUST NOT inherit its obsolete DB or runtime account reset marker. Existing quota-derived blocks, blocked account statuses, runtime cooldowns, error-rule cooldowns and provider-specific safety rules MUST remain authoritative. Persistence MUST retain its optimistic concurrency checks.

#### Scenario: Selection follows successful reset recovery
- **GIVEN** reset recovery clears the account reset marker and marks the account active
- **AND** runtime still holds an earlier future reset marker
- **WHEN** selection constructs and persists state
- **THEN** the obsolete runtime marker is not written back

#### Scenario: Blocked state remains protected
- **GIVEN** an account is RATE_LIMITED or QUOTA_EXCEEDED with a live blocking marker
- **WHEN** selection constructs state
- **THEN** the existing blocking and bounded-retry rules still apply
