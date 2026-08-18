## ADDED Requirements

### Requirement: Service shutdown drains in-flight requests

The server SHALL accept a graceful-shutdown window (`--timeout-graceful-shutdown` / `UVICORN_TIMEOUT_GRACEFUL_SHUTDOWN`, default 75 seconds) and pass it to the ASGI server so SIGTERM lets in-flight requests finish before connections are forced closed. The live launchd job MUST configure `ExitTimeOut` above the drain window so launchd's SIGKILL cannot race the drain.

#### Scenario: Restart with active streams

- **GIVEN** the service receives SIGTERM while streams are in flight
- **WHEN** the drain window has not elapsed
- **THEN** in-flight responses continue to completion and new connections are refused

### Requirement: Runtime sync failures are observable

The runtime sync script SHALL report the real rsync exit code on failure, SHALL retry once after a transient failure, and SHALL emit an explicit ALERT log line when it falls back to the last-good runtime after a persistent failure. The watchdog SHALL log the service plist's mtime and checksum on the first tick a launchd job is found missing.

#### Scenario: Transient external-volume failure

- **GIVEN** the first rsync from the dev repo fails
- **WHEN** the retry succeeds
- **THEN** the sync completes and no ALERT line is emitted

#### Scenario: Persistent sync failure

- **WHEN** rsync fails twice
- **THEN** the log records an ALERT with the real exit code and the service starts on the last-good runtime
