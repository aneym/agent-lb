## ADDED Requirements

### Requirement: Managed default GPT seats
The policy installer SHALL manage gpt-implementer, gpt-explorer and sol-consult with ownership markers and checkpoints without modifying local off-default seats.

#### Scenario: Replacing a local default seat
- **WHEN** a local gpt-implementer is present during installation
- **THEN** its old contents are checkpointed, the managed definition replaces it, and the ownership marker records `agent-lb:gpt-implementer:v1`

#### Scenario: Preserve off-default seats
- **WHEN** local luna-implementer and sonnet-implementer definitions exist
- **THEN** installation leaves their bytes unchanged

#### Scenario: Safe uninstall
- **WHEN** the managed default seats are uninstalled
- **THEN** only owned, unmodified copies are removed
