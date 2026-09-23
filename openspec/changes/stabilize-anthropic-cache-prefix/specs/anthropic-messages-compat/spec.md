## ADDED Requirements

### Requirement: Volatile Claude Code billing metadata does not invalidate a cached prefix

When a Claude Code Messages request contains a per-turn `x-anthropic-billing-header` system block before a system cache-control breakpoint, the proxy MUST forward the billing block after all cacheable system blocks. It MUST preserve the block content and keep the Claude Code identity as the first system block. Other request fields MUST remain unchanged.

#### Scenario: Consecutive turns carry different prompt IDs

- **WHEN** two otherwise identical Messages requests contain different per-turn billing markers before the same cache-control breakpoint
- **THEN** the upstream cacheable prefix through that breakpoint is identical for both requests
- **AND** each request retains its own billing marker after the breakpoint
