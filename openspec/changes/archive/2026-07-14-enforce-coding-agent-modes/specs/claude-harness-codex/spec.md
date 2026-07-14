## ADDED Requirements

### Requirement: Explicit Claude Code launch profiles
The launcher SHALL default normal Claude Code sessions to canonical Fable with high effort when the caller supplies no model or effort, and SHALL force CCDEX sessions to the canonical compatibility model with high effort regardless of caller model or effort arguments.

#### Scenario: Normal Claude Code default
- **WHEN** the normal launcher is invoked without a model or effort override
- **THEN** the executed Claude Code command names canonical Fable and high effort

#### Scenario: CCDEX ignores conflicting controls
- **WHEN** CCDEX is invoked with caller-supplied model or effort controls
- **THEN** the executed Claude Code command names only the canonical compatibility model and high effort

### Requirement: CCDEX child inference isolation
CCDEX MUST reject every Messages inference request whose body does not name the canonical compatibility model. It MUST NOT pass a Claude-model `Agent`, `Workflow`, resume, or other child request to the ordinary Anthropic route, and rejection MUST occur before selecting an Anthropic account or sending upstream inference.

#### Scenario: Claude-model agent request
- **WHEN** a CCDEX session emits a Messages request naming a Claude model
- **THEN** the launcher returns a deterministic non-success error
- **AND** no ordinary Anthropic request is sent

#### Scenario: Canonical GPT request
- **WHEN** a CCDEX session emits a Messages request naming the canonical compatibility model
- **THEN** the launcher rewrites it to the dedicated CCDEX compatibility route

#### Scenario: Token-count request
- **WHEN** a CCDEX session requests Messages token counting
- **THEN** the launcher uses the local compatibility counter without selecting an Anthropic account

## REMOVED Requirements

### Requirement: Selective Claude-model passthrough
**Reason**: Allowing Claude-model Messages requests inside CCDEX made the actual worker model depend on literals embedded in Agent and Workflow definitions, violating the selected GPT-only development mode.

**Migration**: Run Fable-led planning in normal `cc` and dispatch implementation through `ccdex-worker`; inside `ccdex`, use the GPT main loop or GPT/Codex worker fan-out.
