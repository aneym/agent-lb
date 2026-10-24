## ADDED Requirements

### Requirement: Claude Code Muse FULL inference
The service SHALL translate Claude Code Messages requests and tool results to Muse Responses and translate response events back to Messages. The launcher SHALL pin all default and subagent models to muse-wonder-full-1.0, with contributor mode disabled.

#### Scenario: Tool cycle
- **WHEN** Claude Code requests a tool call from Muse FULL and submits its result
- **THEN** the real tool SHALL run and Muse FULL SHALL complete the follow-up turn
- **AND** neither Anthropic nor another model SHALL serve the request
