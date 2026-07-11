## ADDED Requirements

### Requirement: macOS service restart completion is readiness-driven and timed

The macOS LaunchAgent installer MUST wait for the previous localhost listener to release, MUST retry bootstrap using bounded state-driven backoff, and MUST require `/health/ready` before reporting successful installation. It MUST report elapsed timing for the restart phases without exposing environment variable values or credentials.

#### Scenario: Existing service restarts promptly

- **WHEN** the existing LaunchAgent releases its listener and launchd accepts bootstrap before the retry deadline
- **THEN** the installer proceeds without an unconditional fixed cooldown
- **AND** it reports bootout, bootstrap, process startup, and readiness elapsed timing

#### Scenario: Process is live but not ready

- **WHEN** the replacement process responds to liveness but `/health/ready` remains unavailable
- **THEN** the installer does not report success
- **AND** it continues bounded readiness polling or exits with a diagnostic naming the service log
