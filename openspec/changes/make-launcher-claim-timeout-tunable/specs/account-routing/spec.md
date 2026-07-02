## ADDED Requirements

### Requirement: Launcher session-route claim timeout is tunable

The Claude launcher MUST read the session-route claim request timeout from the
`CLAUDE_LB_CLAIM_TIMEOUT` environment variable (float seconds), defaulting to
5.0 seconds when unset or invalid, so high-latency network paths (e.g.
relay-routed VPN links) can be tolerated without code changes.

#### Scenario: High-latency path with raised claim timeout

- **GIVEN** the LB is reachable only over a path where TLS setup takes ~5s
- **AND** `CLAUDE_LB_CLAIM_TIMEOUT=15` is set
- **WHEN** the launcher claims a sticky session route
- **THEN** the claim request waits up to 15s and succeeds instead of timing
  out and falling back to plain claude

#### Scenario: Default behavior unchanged

- **GIVEN** `CLAUDE_LB_CLAIM_TIMEOUT` is unset
- **WHEN** the launcher claims a sticky session route
- **THEN** the claim request uses the previous default timeout of 5.0 seconds
