## ADDED Requirements

### Requirement: Stall stack evidence survives process recovery

On platforms with SIGUSR2, the service SHALL register an idempotent signal handler that appends all Python thread stacks to `~/.agent-lb/forensics/py-stacks.log`. The watchdog SHALL attempt to capture this dump, a native process sample with a configured duration, and a timestamped recovery marker before restarting an unhealthy process. Capture failures MUST NOT prevent the restart. The watchdog SHALL remove the oldest forensic files when the configured file-count limit is exceeded.

#### Scenario: Signal captures Python stacks

- **GIVEN** stack-dump registration has succeeded
- **WHEN** the process receives SIGUSR2
- **THEN** all Python thread stacks are appended to the forensic log
- **AND** a second registration call does not replace the registered destination

#### Scenario: Watchdog collects evidence before recovery

- **GIVEN** the watchdog has identified an unhealthy process for restart
- **WHEN** it performs recovery
- **THEN** it records a timestamped marker and attempts the Python dump and native sample before restarting
- **AND** a failed capture does not suppress the restart
- **AND** file-count pruning retains the newest forensic files up to the configured limit

### Requirement: Event-loop scheduling lag is observable

The service SHALL sample asyncio scheduling drift at a default one-second interval. When Prometheus is available, it SHALL publish the latest nonnegative lag as `agent_lb_event_loop_lag_seconds` and increment `agent_lb_event_loop_lag_events_total` for each sample above the configured warning threshold. The threshold SHALL default to 0.5 seconds. Lag warnings SHALL be rate-limited to at most one per ten seconds without suppressing metric updates.

#### Scenario: Lag remains below the threshold

- **WHEN** a lag sample does not exceed the configured warning threshold
- **THEN** the gauge records that sample
- **AND** the event counter is not incremented
- **AND** no lag warning is logged

#### Scenario: Repeated lag exceeds the threshold

- **WHEN** multiple samples exceed the configured warning threshold within ten seconds
- **THEN** each sample updates the gauge and increments the event counter
- **AND** at most one lag warning is logged during that interval

#### Scenario: Metrics support is absent

- **WHEN** Prometheus metrics are unavailable
- **THEN** lag sampling and warning logging continue without a metrics error
