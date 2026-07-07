## ADDED Requirements

### Requirement: Sticky-session rebinds emit a queryable audit event

The system MUST emit a `sticky_session_rebound` audit event whenever an existing
sticky mapping is rebound to a different account. The event MUST be persisted to
the shared audit-log store so it is queryable by SQL and visible through the
existing audit-log listing surface without a new table or schema migration. The
event details MUST include the sticky kind, old account id, new account id, and
the rebind reason. The raw sticky/affinity key MUST NOT be stored; only a hashed
form of the key may be persisted. The event MUST reuse the standard audit request
id when one is available.

#### Scenario: Rebind audit event is queryable

- **WHEN** an existing sticky pin is rebound to a different account
- **THEN** an audit event with action `sticky_session_rebound` is written to the
  audit-log store
- **AND** it can be retrieved by filtering the audit-log listing on that action

#### Scenario: Rebind audit event does not expose the raw affinity key

- **WHEN** a `sticky_session_rebound` event is recorded
- **THEN** its details carry a hashed sticky key rather than the raw affinity key
- **AND** the details include the sticky kind, old account id, new account id, and
  rebind reason
