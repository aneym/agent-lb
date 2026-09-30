## ADDED Requirements

### Requirement: Launcher-owned readiness
Each launcher-scoped proxy MUST publish and clean up a readiness receipt owned by its parent process, independent of other launchers using the same stable routing session. Routing session identity MUST remain stable for headless cwd affinity. Shared desktop proxy readiness MUST remain unchanged.

#### Scenario: Overlapping headless launchers
- **GIVEN** two launcher processes sharing one headless routing session
- **WHEN** both proxies start and the first proxy stops
- **THEN** the second readiness receipt remains valid and names its own port
- **AND** both proxies retain the same routing session identity
