# runtime-portability Delta

## ADDED Requirements

### Requirement: Launcher shim rides out load-balancer recovery windows

The `claude-lb-launch` intercepting shim MUST retry connection-level upstream
failures (refused, reset, broken pipe — errors where no HTTP response was
produced) with bounded backoff for a default budget of at least 100 seconds,
so that a watchdog-driven service recovery (~65s worst case) never surfaces a
connection error to the agent. Real HTTP responses MUST NOT be retried. The
budget MUST remain overridable via `CLAUDE_LB_SHIM_CONNECT_RETRIES`,
`CLAUDE_LB_SHIM_CONNECT_BACKOFF`, and `CLAUDE_LB_SHIM_CONNECT_BACKOFF_CAP`.

#### Scenario: LB restarts mid-session

- **GIVEN** an agent session proxied through the shim
- **WHEN** agent-lb is unavailable for up to 100 seconds
- **THEN** the shim keeps retrying the request and completes it once the
  service is back, without surfacing a 502 to the agent

#### Scenario: Real HTTP errors pass through

- **WHEN** the upstream returns an HTTP response (including 429/503)
- **THEN** the shim forwards it without retrying
