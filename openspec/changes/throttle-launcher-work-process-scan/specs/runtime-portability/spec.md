# runtime-portability Delta

## ADDED Requirements

### Requirement: The headless stall supervisor scans processes only when idle

The supervised headless launcher MUST NOT scan the process table while agent-lb traffic or transcript writes keep the session alive. It MUST scan at most once per `min(30 s, stall timeout / 4)` while idle or while a tool process it found is running, and MUST scan once before stopping a stalled session.

#### Scenario: Busy session

- **GIVEN** a supervised session whose activity file reports no in-flight request and a fresh progress time
- **WHEN** the child runs for several seconds under a 60 s stall timeout
- **THEN** the launcher runs no `ps`

#### Scenario: Idle session with a running tool

- **GIVEN** a supervised session with no traffic whose child runs a shell tool longer than the stall timeout
- **WHEN** the supervisor reaches the stall timeout
- **THEN** it scans, finds the tool, and keeps the session alive
