## ADDED Requirements

### Requirement: Live quota rejection is authoritative
For OpenAI and Anthropic requests, a primary, secondary, weekly, or additional usage snapshot reporting exhaustion MUST NOT by itself remove an otherwise routable account from selection. The snapshot MAY lower its ranking. If every snapshot is exhausted, the router MUST still attempt eligible accounts, subject to request retry bounds.

A real upstream quota or rate-limit response MUST exclude that account for a bounded retry interval. At expiry, the account MUST become eligible for another real attempt even if a usage snapshot still reports exhaustion. This recovery MUST survive a router restart when the failure marker is persisted. A successful upstream response MUST clear the failure marker. Authentication, paused/deactivated, incompatible-model, and explicit account-pin constraints MUST remain in force.

#### Scenario: Snapshot exhausted but upstream still accepts requests
- **GIVEN** a compatible active account whose usage snapshot reports exhaustion
- **WHEN** no real upstream quota cooldown is active
- **THEN** routing can attempt the account within request retry bounds
- **AND** a successful upstream response clears any previous failure marker
