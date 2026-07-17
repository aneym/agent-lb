## ADDED Requirements

### Requirement: Canonical frontend designer agent installation

The coding-agent policy installer SHALL install the versioned frontend-designer definition with the canonical Opus model into the user's Claude agents directory and SHALL record explicit ownership when it claims that path. Installation MUST checkpoint a pre-existing definition before replacement, MUST preserve unrelated agent files, and MUST converge idempotently. Uninstall MUST remove the definition only when ownership was previously recorded and its installed content still matches the versioned managed content.

#### Scenario: First frontend designer install

- **WHEN** an operator installs the canonical coding-agent policy
- **THEN** `~/.claude/agents/frontend-designer.md` matches the versioned definition
- **AND** its model is Opus

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
