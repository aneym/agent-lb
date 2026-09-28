# macos-menubar Delta

## ADDED Requirements

### Requirement: Menubar tolerates brief health-check failures

After a successful health check, failed checks within a 45-second grace window MUST keep the last known running or degraded status. The status MUST become unreachable (or stopped when the local job is not loaded) only once no health check has succeeded for 45 seconds.

#### Scenario: Transient failure retains last known status

- **GIVEN** the last successful health check reported a running or degraded service less than 45 seconds ago
- **WHEN** a subsequent health check fails
- **THEN** the menubar retains the last known running or degraded status

#### Scenario: Grace window expires

- **GIVEN** no health check has succeeded for 45 seconds
- **WHEN** another health check fails
- **THEN** the menubar reports unreachable, or stopped if the local job is not loaded
