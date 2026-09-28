# deployment-installation Delta

## ADDED Requirements

### Requirement: macOS LaunchAgents run at elevated scheduling priority

The service, TCP front, and Claude Desktop proxy LaunchAgents MUST default to `Nice = -10` and `ProcessType = Interactive` so their event loops remain responsive under host load. Regenerating the service plist MUST preserve an existing integer `Nice` and string `ProcessType` operator override, including `Nice = 0`.

#### Scenario: Fresh LaunchAgents receive priority defaults

- **GIVEN** no existing service LaunchAgent plist
- **WHEN** the service, front, and desktop-proxy installers generate their plists
- **THEN** each plist contains `Nice = -10` and `ProcessType = Interactive`

#### Scenario: Service operator overrides survive reinstall

- **GIVEN** an existing service plist with `Nice = 0` and `ProcessType = Standard`
- **WHEN** the service installer regenerates the plist
- **THEN** the plist retains `Nice = 0` and `ProcessType = Standard`
