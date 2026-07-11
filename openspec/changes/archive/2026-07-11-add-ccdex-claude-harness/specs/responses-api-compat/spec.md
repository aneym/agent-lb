## ADDED Requirements

### Requirement: Claude Messages compatibility over Responses
Agent-lb SHALL provide a dedicated compatibility surface that translates Claude Code Messages requests into typed Responses requests and translates Responses JSON/SSE results back into valid Anthropic Messages JSON/SSE results.

#### Scenario: Fixed outbound controls
- **WHEN** a valid compatibility Messages request is translated
- **THEN** the outbound Responses request uses `gpt-5.6-sol`, `reasoning.effort=high`, `service_tier=priority`, `store=false`, and streaming when requested

#### Scenario: Upstream error
- **WHEN** the Responses route fails before or during streaming
- **THEN** the bridge returns a valid Anthropic error envelope without a duplicate terminal sequence

### Requirement: Requested and actual fast-tier observability
Compatibility request logs MUST record requested priority separately from the actual upstream service tier and MUST NOT label default-tier execution as fast.

#### Scenario: Priority degrades to default
- **WHEN** the request asks for priority and upstream reports default
- **THEN** logs show requested `priority`, actual `default`, and billable tier according to existing accounting rules
