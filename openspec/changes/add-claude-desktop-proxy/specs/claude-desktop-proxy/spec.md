## ADDED Requirements

### Requirement: Durable shared Claude Desktop Code proxy
The system SHALL provide a macOS LaunchAgent installer for a loopback-only shared HTTPS proxy used by Claude Desktop's embedded Claude Code runtime. The installer MUST start the proxy on a fixed operator-configurable port, MUST verify the proxied Anthropic health endpoint before changing Claude settings, and MUST refuse to take over a listener it does not own.

#### Scenario: Healthy install cuts settings over after readiness
- **WHEN** the operator installs the shared proxy and the configured loopback port is available
- **THEN** launchd starts a persistent KeepAlive proxy
- **AND** the installer verifies an Anthropic health request through that proxy
- **AND** only then atomically updates Claude settings to use the verified port and local CA

#### Scenario: Foreign listener blocks installation
- **WHEN** an unrelated process already owns the configured loopback port
- **THEN** the installer exits nonzero without stopping that process
- **AND** Claude settings remain unchanged

### Requirement: Preservation-safe settings ownership
The installer MUST preserve unrelated Claude settings and environment entries. It MUST checkpoint the values it owns before changing them, MUST reject conflicting pre-existing proxy or CA values, and uninstall MUST restore or remove an owned value only when the current value still matches the value written by the installer.

#### Scenario: Existing unrelated configuration survives install and uninstall
- **GIVEN** Claude settings contain unrelated permissions, hooks, models, and environment variables
- **WHEN** the operator installs and later uninstalls the shared proxy
- **THEN** every unrelated value remains unchanged
- **AND** prior owned values are restored from the checkpoint

#### Scenario: User changes an owned value after installation
- **WHEN** an operator changes a proxy or CA setting after installation
- **AND** then runs uninstall
- **THEN** uninstall preserves the operator's newer value rather than overwriting it from the checkpoint

### Requirement: Shared proxy preserves server routing authority and session identity
The shared proxy MUST forward request paths and requested model values without client-side GPT alias rewriting. It MUST preserve inbound session identity and MUST NOT synthesize one machine-wide session identifier when the request has none.

#### Scenario: Supported GPT alias reaches canonical Messages endpoint
- **WHEN** the embedded Code runtime sends a Messages request containing a supported GPT alias
- **THEN** the shared proxy forwards the canonical Messages path and body unchanged
- **AND** the agent-lb server decides whether to invoke its compatibility bridge

#### Scenario: Request without explicit session identity
- **WHEN** a request contains no session identity header or payload metadata
- **THEN** the shared proxy forwards it without adding a shared machine-level session ID

### Requirement: Explicit launcher bypass survives shared settings
The Claude launcher MUST bypass the shared proxy for `api.anthropic.com` whenever agent-lb routing is explicitly disabled or the launcher takes its documented plain-Claude fallback path.

#### Scenario: Routing disabled
- **WHEN** `CLAUDE_LB_DISABLE=1` launches Claude while shared proxy settings are installed
- **THEN** the child process receives both uppercase and lowercase no-proxy exclusions for `api.anthropic.com`
- **AND** its Anthropic traffic is not recaptured by the shared proxy

### Requirement: Preview and rollback are operator-visible
The installer SHALL provide a non-mutating preview and a safe uninstall command. It MUST report the LaunchAgent label, port, upstream, settings path, and rollback behavior without printing credentials.

#### Scenario: Preview makes no changes
- **WHEN** the operator runs the installer with `--print`
- **THEN** it describes the planned LaunchAgent and settings edits
- **AND** it does not write files, start processes, or change Claude settings
