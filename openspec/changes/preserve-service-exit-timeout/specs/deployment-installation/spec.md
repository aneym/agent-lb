# deployment-installation Delta

## ADDED Requirements

### Requirement: Service plist retains a graceful shutdown window

The macOS service LaunchAgent installer MUST preserve an existing integer `ExitTimeOut`. If there is no valid existing value, it MUST set `ExitTimeOut` to the resolved `UVICORN_TIMEOUT_GRACEFUL_SHUTDOWN` plus 30 seconds, using 75 seconds for the graceful-shutdown duration if the variable is missing or not an integer. This gives lb-restart's drain and launchd's SIGKILL deadline room for in-flight streams.

#### Scenario: Customized shutdown timeout survives reinstall

- **GIVEN** the existing service plist contains `ExitTimeOut = 330`
- **WHEN** the installer regenerates the plist
- **THEN** `ExitTimeOut` remains 330

#### Scenario: Fresh service gets a shutdown timeout

- **GIVEN** there is no existing service plist
- **WHEN** the installer generates the plist
- **THEN** `ExitTimeOut` is 105

#### Scenario: Service environment determines the shutdown timeout

- **GIVEN** an existing service plist has `UVICORN_TIMEOUT_GRACEFUL_SHUTDOWN = 300` in its environment and no `ExitTimeOut`
- **WHEN** the installer regenerates the plist
- **THEN** `ExitTimeOut` is 330
