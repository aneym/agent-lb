## ADDED Requirements

### Requirement: Explicit native artifact owner
The artifact bridge SHALL use the configured Anthropic owner account and SHALL fail closed when the native Claude login email does not match. It SHALL NOT route artifact ownership through inference account load balancing.

#### Scenario: Correct owner with non-Anthropic inference
- **WHEN** a GPT or Kimi client calls an artifact tool through the bridge
- **THEN** the official native Claude MCP host performs the operation using the designated claude.ai login
- **AND** inference model selection does not change artifact ownership

#### Scenario: Wrong login
- **WHEN** the native login does not match the owner designation
- **THEN** the bridge refuses startup without falling back to another account

### Requirement: Artifact-only tool surface
The bridge SHALL expose only Artifact, ArtifactComments and ArtifactData, and SHALL reject other tool calls.

#### Scenario: Other native tool requested
- **WHEN** a client requests a native shell or file tool
- **THEN** the bridge returns a protocol error without forwarding the call
