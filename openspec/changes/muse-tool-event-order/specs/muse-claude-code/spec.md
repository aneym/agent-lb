## ADDED Requirements

### Requirement: Exactly once parallel tool emission
The Muse Messages adapter SHALL emit each upstream function call exactly once with its complete arguments even when added events for multiple calls precede done events.

#### Scenario: Interleaved parallel calls
- **WHEN** Muse emits added A, added B, done A, done B
- **THEN** Claude Code SHALL receive one complete tool call A and one complete tool call B
