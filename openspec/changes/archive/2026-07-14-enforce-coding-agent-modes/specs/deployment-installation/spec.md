## ADDED Requirements

### Requirement: Claude Code client wiring includes the CCDEX worker
The project SHALL provide a deterministic installer for the canonical coding-agent policy and the `cc`, `ccdex`, and `ccdex-worker-mcp` client executables, and SHALL support user-scoped Claude Code MCP registration of `ccdex-worker-mcp`. Re-running installation MUST converge without duplicating registrations, and removal MUST be explicit rather than coupled to service uninstall.

#### Scenario: First client install
- **WHEN** an operator installs Claude Code client wiring
- **THEN** the three client entry points resolve to the current checkout
- **AND** the host-neutral coding-agent policy path resolves to the versioned repository policy
- **AND** Claude Code lists a connected `ccdex-worker` stdio MCP server using the installed worker executable

#### Scenario: Repeated client install
- **WHEN** the client installer runs again for the same checkout
- **THEN** the policy link, executable links, and MCP registration converge on the same targets without duplicate server names

#### Scenario: Preview install
- **WHEN** the client installer runs in preview mode
- **THEN** it reports the policy link, executable links, and MCP registration it would change without mutating user configuration
