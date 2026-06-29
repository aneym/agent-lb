## ADDED Requirements

### Requirement: Local GLM launcher uses agent-lb
The local `glm` shell launcher SHALL point Claude Code at the local agent-lb Anthropic-compatible base URL instead of directly at Z.AI. The launcher SHALL set GLM model defaults suitable for the GLM Coding Plan and SHALL use only a downstream proxy credential or local placeholder as `ANTHROPIC_AUTH_TOKEN`; the real Z.AI API key SHALL come from the selected stored GLM account.

#### Scenario: GLM command routes through local agent-lb
- **WHEN** an operator starts Claude Code with the `glm` shell command
- **THEN** Claude Code sends Anthropic-compatible Messages requests to agent-lb
- **AND** the requested model starts with `glm-`
- **AND** the local launcher does not expose the stored Z.AI account API key in process arguments
