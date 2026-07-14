## MODIFIED Requirements

### Requirement: Claude Code client wiring includes the CCDEX worker

The project SHALL provide a deterministic installer for the canonical coding-agent policy and the `cc`, `ccdex`, and `ccdex-worker-mcp` client executables, and SHALL support user-scoped Claude Code MCP registration of `ccdex-worker-mcp`. Installation MUST converge the routing-owned portions of global Claude and Codex instructions, MUST set direct Claude Code to the canonical Fable model, and MUST install and register the versioned CCDEX GPT-only guard hook. Installation MUST preserve unrelated Markdown sections, JSON keys, permission rules, hooks, environment values, and machine-specific configuration. Re-running installation MUST converge without duplicating managed blocks, hooks, or registrations, and removal MUST be explicit rather than coupled to service uninstall.

#### Scenario: First client install

- **WHEN** an operator installs Claude Code client wiring
- **THEN** the three client entry points resolve to the current checkout
- **AND** the host-neutral coding-agent policy path resolves to the versioned repository policy
- **AND** the global Claude and Codex instruction files contain one canonical routing adapter each
- **AND** direct Claude Code defaults to the canonical Fable model
- **AND** the user CCDEX guard hook resolves to the versioned repository hook and is registered once for relevant pre-tool calls
- **AND** Claude Code lists a connected `ccdex-worker` stdio MCP server using the installed worker executable

#### Scenario: Existing machine-specific configuration is reconciled

- **GIVEN** global Claude and Codex files contain legacy routing prose and unrelated instructions
- **AND** Claude settings contain unrelated keys, environment values, permissions, and hooks
- **WHEN** the client installer runs
- **THEN** known legacy routing prose is replaced by the canonical adapters
- **AND** unrelated Markdown sections and JSON values remain present
- **AND** unrelated hooks remain registered

#### Scenario: Repeated client install

- **WHEN** the client installer runs again for the same checkout
- **THEN** the policy link, executable links, managed routing blocks, model field, guard hook, and MCP registration converge on the same targets
- **AND** it does not create duplicate managed blocks, hook registrations, or server names

#### Scenario: Preview install

- **WHEN** the client installer runs in preview mode
- **THEN** it reports the policy link, executable links, routing adapters, model field, guard hook, and MCP registration it would change
- **AND** it does not mutate user configuration

#### Scenario: Conflicting existing guard hook is preserved

- **GIVEN** the CCDEX guard hook path contains a regular user file
- **WHEN** installation first claims that path
- **THEN** the installer preserves the file at a deterministic backup path before linking the versioned hook
- **AND** it refuses to overwrite the path when that backup already exists
