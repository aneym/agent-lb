## ADDED Requirements

### Requirement: Opus frontend designer seat

The canonical Claude Code frontend-designer child SHALL use Opus for design direction and visual critique and MUST NOT inherit or explicitly select the Fable driver model.

#### Scenario: Frontend designer dispatch

- **WHEN** Claude Code dispatches the canonical `frontend-designer` agent without a per-invocation model override
- **THEN** the child resolves to the canonical Opus model
- **AND** the request does not consume Fable capacity

#### Scenario: No Fable fallback configuration

- **WHEN** the canonical frontend-designer definition is installed
- **THEN** its model field selects Opus
- **AND** the definition does not configure Fable as a fallback
