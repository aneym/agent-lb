# deployment-installation — delta

## ADDED Requirements

### Requirement: Fable client installation

The Claude client installer SHALL install the `fable` launch profile alongside the canonical `cc` client. It MUST refuse to overwrite a pre-existing non-symlink target without preserving it as a backup, MUST converge idempotently, and MUST remove the installed link on uninstall only when it points at the repository client it installed.

#### Scenario: First fable install

- **WHEN** an operator installs the Claude clients
- **THEN** the `fable` client is linked into the client bin directory alongside `cc`

#### Scenario: Pre-existing fable executable

- **GIVEN** a non-symlink `fable` executable already exists in the client bin directory
- **WHEN** the operator installs the Claude clients
- **THEN** the pre-existing executable is preserved as a backup before replacement
- **AND** installation fails rather than destroying it when a backup already exists

#### Scenario: Uninstall is ownership-gated

- **GIVEN** the installed `fable` link points at the repository client
- **WHEN** the operator uninstalls the Claude clients
- **THEN** the link is removed
- **AND** an unrelated `fable` executable is left in place

### Requirement: Canonical plan reviewer agent installation

The coding-agent policy installer SHALL install a versioned plan-reviewer definition whose model selector is `claude-planner` and whose effort is `high` into the user's Claude agents directory and SHALL record explicit ownership when it claims that path. Installation MUST checkpoint a pre-existing definition before replacement, MUST preserve unrelated agent files, and MUST converge idempotently. Uninstall MUST remove the definition only when ownership was previously recorded and its installed content still matches the versioned managed content.

#### Scenario: First plan reviewer install

- **WHEN** an operator installs the canonical coding-agent policy
- **THEN** `~/.claude/agents/plan-reviewer.md` matches the versioned definition
- **AND** its model selector is `claude-planner`
- **AND** its effort is `high`

#### Scenario: Existing plan reviewer definition

- **GIVEN** a machine-local plan-reviewer definition already exists
- **WHEN** the installer converges the canonical policy
- **THEN** the existing definition is copied into the installation checkpoint before replacement
- **AND** unrelated Claude agent definitions remain unchanged

#### Scenario: Plan reviewer uninstall preserves unmanaged or customized content

- **GIVEN** the plan-reviewer definition is unowned or differs from the installed managed content
- **WHEN** the operator uninstalls the managed coding-agent policy
- **THEN** the plan-reviewer definition remains present
- **AND** the operator is told why it was preserved

### Requirement: Fable mode and plan reviewer route verification

The project SHALL provide deterministic verification that the `fable` client targets `claude-fable-5`, swaps only the driver and Opus/Fable default slots, runs through the canonical launcher, and is installed by the client installer, and that the canonical plan reviewer selects `claude-planner` with high effort, disallows writing, and matches its installed, ownership-marked definition.

#### Scenario: Installed Fable mode is converged

- **WHEN** the routing verification runs on a converged machine
- **THEN** every Fable-client and plan-reviewer check passes
- **AND** the canonical policy documents the Fable driver mode

#### Scenario: Seat slot regression is caught

- **GIVEN** the `fable` client is changed to write the Sonnet, Haiku, or subagent model slot
- **WHEN** the routing verification runs
- **THEN** verification fails
