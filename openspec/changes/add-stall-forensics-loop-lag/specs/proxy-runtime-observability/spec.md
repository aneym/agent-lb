## ADDED Requirements

### Requirement: Stalls produce bounded diagnostic evidence
The app SHALL append all-thread Python stacks to the forensics log when signaled with SIGUSR2. It SHALL sample event-loop scheduling drift, expose the latest lag and threshold-crossing count on the existing Prometheus registry, and rate-limit warnings above the configured threshold. Before restarting an unhealthy app, the watchdog SHALL request a stack dump and capture a bounded native sample and event marker; failures to collect evidence SHALL NOT prevent restart.

#### Scenario: Loop stalls before the watchdog restarts it
- **WHEN** the app's event loop is delayed beyond the warning threshold and the watchdog detects an unhealthy process
- **THEN** the lag metric and event counter record the delay, with rate-limited warnings
- **AND** the watchdog attempts to save Python stacks, a native sample and an event marker before restart
- **AND** a failed forensics capture does not block the restart
