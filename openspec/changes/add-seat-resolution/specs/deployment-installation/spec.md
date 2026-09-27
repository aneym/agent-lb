## ADDED Requirements

### Requirement: Every seat definition is managed
The policy installer SHALL manage every seat definition in `config/coding-agents/agents/`, off-default seats included, with ownership markers and checkpoints.

#### Scenario: Replacing an off-default definition
- **WHEN** a local luna-implementer definition differs from the managed one
- **THEN** its old bytes are checkpointed, the managed definition replaces it, and the marker records `agent-lb:luna-implementer:v1`
