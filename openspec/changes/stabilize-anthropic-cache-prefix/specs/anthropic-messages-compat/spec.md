## ADDED Requirements

### Requirement: Volatile Claude Code billing metadata does not invalidate a cached prefix

When a Claude Code Messages request contains a per-turn `x-anthropic-billing-header` system block before a system cache-control breakpoint, the proxy MUST forward the billing block after all cacheable system blocks. It MUST keep the Claude Code identity as the first system block. Other request fields MUST remain unchanged.

#### Scenario: Consecutive turns carry different prompt IDs

- **WHEN** two otherwise identical Messages requests contain different per-turn billing markers before the same cache-control breakpoint
- **THEN** the upstream cacheable prefix through that breakpoint is identical for both requests
- **AND** a request without an identifiable session retains its own billing marker after the breakpoint

### Requirement: Session billing metadata remains stable through message cache breakpoints

For Claude Code requests with an identifiable session, the proxy MUST retain the first observed `cch` and `cc_prompt_id` billing-marker values for subsequent requests in that session. It MUST change no other billing-marker text or request field. The session-value cache MUST be bounded and expire idle sessions. Requests without an identifiable session MUST retain their original billing marker.

#### Scenario: A resumed conversation reuses its message prefix

- **WHEN** the second request in a session adds a turn after a previously cache-controlled message and supplies new billing-marker values
- **THEN** the forwarded system blocks and prior message prefix are byte-equivalent to the first request
- **AND** only the two volatile billing-marker values are replaced with the session's first values
