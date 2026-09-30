## ADDED Requirements

### Requirement: Stand-in state survives restarts and reports returns
The service SHALL persist stand-ins to AGENT_LB_STAND_IN_FILE or ~/.agent-lb/stand-ins.json, reading and writing on each change under a process lock without a cache. Failure records SHALL contain session, lane, intent, intended and running models, effort, initial since, last_at, request count, code/message reason, and the UTC expected_return reset or null. Every turn SHALL try Anthropic first; its first success SHALL move the record to recent with returned_at, retaining the newest 50 returns. GET /api/pools/stand-ins SHALL return active and recent lists under the existing dashboard dependencies.

#### Scenario: A session returns when Claude refills
- **GIVEN** a persisted active stand-in with two failed Anthropic turns
- **WHEN** the next Anthropic turn succeeds
- **THEN** the active record moves to recent with returned_at
- **AND** the turn is answered by Claude without a standing-in response header

#### Scenario: Repeated failures preserve the initial timestamp
- **GIVEN** an active stand-in record
- **WHEN** another eligible failure occurs
- **THEN** its request count increases and reason, expected_return, and last_at update
- **AND** since remains unchanged
