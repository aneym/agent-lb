## ADDED Requirements

### Requirement: Event-loop stalls self-document while in progress

A watchdog thread outside the event loop MUST observe a heartbeat stamped by the in-loop lag sampler and, when the heartbeat is at least 5 seconds stale, append a timestamped all-threads stack dump to the stall-forensics log while the stall is still in progress, rate-limited to at most one dump per 60 seconds. Dump writes MUST be best-effort: a full disk or unwritable log MUST NOT terminate the watchdog or the service.

#### Scenario: A multi-second stall is captured without operator action

- **GIVEN** the service event loop is blocked for 5 seconds or more
- **WHEN** the watchdog thread observes the stale heartbeat
- **THEN** a timestamped all-threads stack dump is appended to the forensics log during the stall

#### Scenario: A healthy loop produces no dumps

- **GIVEN** the event loop stamps its heartbeat every second
- **WHEN** the watchdog thread runs
- **THEN** no stack dump is written
