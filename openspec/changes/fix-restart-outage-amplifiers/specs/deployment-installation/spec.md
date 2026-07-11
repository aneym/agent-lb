# deployment-installation Delta

## ADDED Requirements

### Requirement: Watchdog does not kill a booting service instance

The watchdog MUST NOT kickstart the service while its current process is still
within a configurable boot-grace window (`AGENT_LB_WATCHDOG_BOOT_GRACE`,
default 240 seconds), because cold boot to first accepted request can take
60–80+ seconds under host load and a kick during boot restarts the clock and
doubles the outage. The unhealthy counter MUST keep accumulating during the
skip so a process that remains unhealthy past the boot grace is kicked on the
next eligible tick. The post-kick grace (`AGENT_LB_WATCHDOG_KICK_GRACE`) MUST
default to at least the boot-grace window.

#### Scenario: Booting replacement is not kicked

- **GIVEN** the launchd job is bootstrapped with a running pid younger than the
  boot-grace window
- **AND** the health endpoint has failed for the kick threshold of consecutive
  ticks
- **WHEN** the watchdog evaluates the kick
- **THEN** it skips the kickstart and records the skip without resetting the
  unhealthy counter

#### Scenario: Hung old process is still kicked

- **GIVEN** the launchd job is bootstrapped with a running pid older than the
  boot-grace window
- **AND** the health endpoint has failed for the kick threshold of consecutive
  ticks
- **WHEN** the watchdog evaluates the kick
- **THEN** it kickstarts the service

### Requirement: Watchdog bounds launchd service log growth

The watchdog MUST rotate the service's launchd stdout/stderr log files when
they exceed a configurable size (`AGENT_LB_WATCHDOG_SERVICE_LOG_MAX_MB`,
default 256). Rotation MUST copy then truncate the live file in place (never
rename it, since launchd holds an append-mode descriptor) and MUST compress
the rotated copy, keeping one generation.

#### Scenario: Oversized service log is rotated

- **GIVEN** a service log file larger than the configured maximum
- **WHEN** a watchdog tick runs
- **THEN** the live file is truncated in place and a compressed rotated copy
  exists alongside it

## MODIFIED Requirements

### Requirement: macOS LaunchAgent install never leaves the service booted out

After booting out the existing job, `scripts/install-service.sh` MUST proceed
to bootstrap the regenerated plist even when localhost port 2455 has not freed
within the post-bootout wait window (configurable via
`AGENT_LB_INSTALL_PORT_FREE_TIMEOUT_SECONDS`, default 30 seconds). A
still-draining old process MUST downgrade the previous hard failure to a
warning: launchd's KeepAlive retries the bind until the port frees. The
installer MUST NOT exit between bootout and bootstrap in a way that leaves no
job loaded.

#### Scenario: Draining process does not abort the install

- **GIVEN** the existing job was booted out and the old process still holds
  localhost:2455 after the wait window
- **WHEN** the installer continues
- **THEN** it emits a warning, writes the plist, and bootstraps the job

#### Scenario: Port frees promptly

- **GIVEN** the old process releases the port within the wait window
- **WHEN** the installer continues
- **THEN** it bootstraps without emitting the busy-port warning
