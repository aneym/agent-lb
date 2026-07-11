# deployment-installation Delta

## ADDED Requirements

### Requirement: Watchdog revives an un-bootstrapped launchd service

The repository MUST version the host watchdog script at `scripts/watchdog.sh` as
the canonical source for `~/.agent-lb/bin/watchdog.sh`. When the main launchd
job is not bootstrapped, the service plist exists, and no pause file is present,
the watchdog MUST re-bootstrap the service after a configurable number of
consecutive ticks (default 2). The pause file MUST be the only signal that
downtime is intentional.

#### Scenario: Failed deploy leaves the service booted out

- **GIVEN** the launchd job is not bootstrapped and no pause file exists
- **WHEN** the watchdog observes the missing job for 2 consecutive ticks
- **THEN** it bootstraps the service from the plist

#### Scenario: Single missing tick does not trigger revival

- **GIVEN** the launchd job is not bootstrapped and no pause file exists
- **WHEN** the watchdog observes the missing job for the first tick
- **THEN** it records the observation without bootstrapping

#### Scenario: Pause file marks downtime as intentional

- **GIVEN** the pause file exists
- **WHEN** the watchdog runs while the job is not bootstrapped
- **THEN** it exits without bootstrapping or kickstarting
