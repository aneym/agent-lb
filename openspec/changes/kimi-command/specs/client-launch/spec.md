## ADDED Requirements

### Requirement: Portable subscribed Kimi launch
The Kimi launcher SHALL default to kimi-for-coding through Agent LB, honor explicit model and endpoint overrides, and SHALL NOT require a machine-specific plugin directory.

#### Scenario: Launch from Book
- WHEN the operator invokes kimi without a model override
- THEN the Claude Code harness SHALL request kimi-for-coding through the configured Agent LB endpoint

#### Scenario: Explicit model
- WHEN the operator supplies a model override
- THEN the driver and Kimi driver model environment SHALL agree on that model
