## MODIFIED Requirements

### Requirement: Claude Code client wiring includes the CCDEX worker

The project SHALL provide a deterministic installer for the canonical coding-agent policy and the `cc`, `ccdex`, and `ccdex-worker-mcp` client executables, and SHALL support user-scoped Claude Code MCP registration of `ccdex-worker-mcp`. Installation MUST converge the routing-owned portions of global Claude and Codex instructions, MUST set direct Claude Code to `claude-opus-5`, MUST persist high effort with Claude Code's supported `effortLevel` setting, and MUST install and register the versioned CCDEX GPT-only guard hook. Installation MUST preserve unrelated Markdown sections, JSON keys, permission rules, hooks, environment values, and machine-specific configuration. Re-running installation MUST converge without duplicating managed blocks, hooks, or registrations, and removal MUST be explicit rather than coupled to service uninstall.

#### Scenario: First client install
- **WHEN** an operator installs Claude Code client wiring
- **THEN** the three client entry points resolve to the current checkout
- **AND** the host-neutral coding-agent policy path resolves to the versioned repository policy
- **AND** the global Claude and Codex instruction files contain one canonical routing adapter each
- **AND** direct Claude Code defaults to `claude-opus-5`
- **AND** direct Claude Code persistent effort defaults to `high`
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

### Requirement: Canonical frontend designer agent installation

The coding-agent policy installer SHALL install the versioned frontend-designer definition whose Claude Code Opus selector resolves to `claude-opus-5` into the user's Claude agents directory and SHALL record explicit ownership when it claims that path. Installation MUST checkpoint a pre-existing definition before replacement, MUST preserve unrelated agent files, and MUST converge idempotently. Uninstall MUST remove the definition only when ownership was previously recorded and its installed content still matches the versioned managed content.

#### Scenario: First frontend designer install

- **WHEN** an operator installs the canonical coding-agent policy
- **THEN** `~/.claude/agents/frontend-designer.md` matches the versioned definition
- **AND** its model selector resolves to `claude-opus-5`

#### Scenario: Existing frontend designer definition

- **GIVEN** a machine-local frontend-designer definition already exists
- **WHEN** the installer converges the canonical policy
- **THEN** the existing definition is copied into the installation checkpoint before replacement
- **AND** unrelated Claude agent definitions remain unchanged

#### Scenario: Repeated and preview installation

- **WHEN** the installer runs repeatedly or in preview mode
- **THEN** repeated installation is byte-stable
- **AND** preview reports any designer-definition change without mutating it

#### Scenario: Uninstall after local customization

- **GIVEN** the installed managed frontend-designer definition was modified after installation
- **WHEN** the operator uninstalls the managed coding-agent policy
- **THEN** the customized definition remains present
- **AND** the operator is told it was preserved

#### Scenario: Uninstall never-managed identical definition

- **GIVEN** a user-created frontend-designer definition is byte-identical to the versioned definition
- **AND** the installer has never recorded ownership of it
- **WHEN** the operator uninstalls the managed coding-agent policy
- **THEN** the user-created definition remains present
- **AND** the operator is told it was preserved as unmanaged

## ADDED Requirements

### Requirement: Canonical planner agent installation

The coding-agent policy installer SHALL install a versioned planner definition whose model selector is `claude-planner` and whose effort is `high` into the user's Claude agents directory and SHALL record explicit ownership when it claims that path. Installation MUST checkpoint a pre-existing definition before replacement, MUST preserve unrelated agent files, and MUST converge idempotently. Uninstall MUST remove the definition only when ownership was previously recorded and its installed content still matches the versioned managed content.

#### Scenario: First planner install

- **WHEN** an operator installs the canonical coding-agent policy
- **THEN** `~/.claude/agents/planner.md` matches the versioned definition
- **AND** its model selector is `claude-planner`
- **AND** its effort is `high`

#### Scenario: Existing planner definition

- **GIVEN** a machine-local planner definition already exists
- **WHEN** the installer converges the canonical policy
- **THEN** the existing definition is copied into the installation checkpoint before replacement
- **AND** unrelated Claude agent definitions remain unchanged

#### Scenario: Planner uninstall preserves unmanaged or customized content

- **GIVEN** the planner definition is unowned or differs from the installed managed content
- **WHEN** the operator uninstalls the managed coding-agent policy
- **THEN** the planner definition remains present
- **AND** the operator is told why it was preserved

### Requirement: Canonical Claude Code route verification

The project SHALL provide deterministic verification that the installed direct Claude Code model is `claude-opus-5` with high effort, the canonical planner selects `claude-planner` with high effort, the frontend-designer resolves to `claude-opus-5`, and the GPT/Sol compatibility and worker seats retain their configured routes.

#### Scenario: Installed routing is converged

- **WHEN** the routing verifier checks a correctly installed policy
- **THEN** it reports direct Claude Code and frontend-designer as Opus 5
- **AND** it reports the planner alias as Fable-primary with scoped Opus 5 fallback
- **AND** it reports the expected unchanged GPT/Sol routes

### Requirement: Opus 5 route fallback and cost attribution

The omitted-model Anthropic session-route fallback SHALL select `claude-opus-5`, and Anthropic usage pricing SHALL resolve canonical and versioned Opus 5 model IDs to $5 per million input tokens and $25 per million output tokens with the established cache multipliers.

#### Scenario: Omitted-model session claim

- **WHEN** a client claims an Anthropic session route without a model
- **THEN** the response and account selection use `claude-opus-5`

#### Scenario: Opus 5 usage cost

- **WHEN** usage analytics records a canonical or versioned Opus 5 model ID
- **THEN** cost attribution uses the Opus 5 input, output, cache-write, and cache-read rates
- **AND** existing Fable pricing remains unchanged

#### Scenario: Legacy Claude route remains

- **WHEN** direct Claude Code or frontend-designer still resolves to Fable 5 or Opus 4.8
- **THEN** routing verification fails and identifies the stale route
