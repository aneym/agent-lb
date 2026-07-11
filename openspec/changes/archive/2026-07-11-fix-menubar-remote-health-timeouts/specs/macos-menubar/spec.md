## MODIFIED Requirements

### Requirement: Menu-bar read deadlines account for service locality

The macOS menu-bar client MUST use a short bounded health-probe deadline for loopback services, a longer bounded dashboard-read deadline for healthy local database work, and bounded remote deadlines that cover cold remote connection setup. A healthy health or dashboard response that completes within its applicable deadline MUST update service or section state without marking the service unreachable or adding a retry state.

#### Scenario: Loopback service fails fast

- **WHEN** the configured service host is `127.0.0.1`, `localhost`, or `::1`
- **THEN** the client uses the 3-second request / 5-second resource health-probe envelope

#### Scenario: Healthy cold loopback dashboard read exceeds the health deadline

- **GIVEN** the configured service host is `127.0.0.1`, `localhost`, or `::1`
- **WHEN** a healthy dashboard response takes longer than the loopback health deadline but completes within the 15-second request / 20-second resource dashboard deadline
- **THEN** the client accepts the response and does not show a retry state for that fetch

#### Scenario: Cold Tailnet health response exceeds the local deadline

- **GIVEN** the configured service host is remote
- **WHEN** a healthy health response takes longer than the local request deadline but completes within the remote deadline
- **THEN** the client accepts the response and does not classify the service as unreachable

#### Scenario: Tailnet dashboard response exceeds the local deadline

- **GIVEN** the configured service host is remote
- **WHEN** a healthy dashboard response takes longer than the local request deadline but completes within the remote deadline
- **THEN** the client accepts the response and does not show a retry state for that fetch

#### Scenario: Remote health probe exceeds its bounded deadline

- **WHEN** a remote health probe does not complete within the remote deadline
- **THEN** the client records a genuine health failure and classifies the remote service as unreachable

#### Scenario: Remote dashboard read exceeds its bounded deadline

- **WHEN** a remote dashboard read does not complete within the remote deadline
- **THEN** the client records a genuine completed timeout failure for that section
