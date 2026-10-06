## ADDED Requirements

### Requirement: Codex seats get a bounded isolated home
The seat home writer SHALL use workspace-write with network access, disable provider websocket support and legacy websocket feature flags, and keep sessions local rather than linking the global archive. Auth MAY be linked but SHALL NOT be read or printed. Implicit apps, plugins and MCP servers SHALL NOT be copied.

#### Scenario: Fresh home from a websocket-enabled source
- **WHEN** the writer builds an isolated home
- **THEN** HTTP transport and network-enabled workspace sandbox settings are written with a real local sessions directory

#### Scenario: Old shared sessions link
- **WHEN** a previous job home links sessions to the global archive
- **THEN** only the link is removed, the archive remains untouched and the local sessions directory is created

#### Scenario: Resume a local job
- **WHEN** the home writer runs again for the same job
- **THEN** its existing local rollouts remain available

### Requirement: Browser capture does not expand the default seat sandbox
Browser capture requiring macOS bootstrap services SHALL be handed to an outside-sandbox browser process rather than removing the default seat filesystem boundary.

#### Scenario: Chromium Mach bootstrap denied
- **WHEN** an in-sandbox Chromium launch fails with bootstrap_check_in or SIGTRAP
- **THEN** the seat reports the evidence and requests the authorized external capture path
