## ADDED Requirements

### Requirement: Menu-bar read deadlines account for service locality

The macOS menu-bar client MUST use a short bounded read deadline for loopback
services and a longer bounded read deadline for remote services. A healthy
remote response that completes within the remote deadline MUST update its
section without adding a retry state.

#### Scenario: Loopback service fails fast

- **WHEN** the configured service host is `127.0.0.1`, `localhost`, or `::1`
- **THEN** the client uses the local dashboard-read timeout envelope

#### Scenario: Tailnet response exceeds the local deadline

- **GIVEN** the configured service host is remote
- **WHEN** a healthy dashboard response takes longer than the local request
  deadline but completes within the remote deadline
- **THEN** the client accepts the response and does not show a retry state for
  that fetch

#### Scenario: Remote read exceeds its bounded deadline

- **WHEN** a remote dashboard read does not complete within the remote deadline
- **THEN** the client records a genuine completed timeout failure for that
  section
