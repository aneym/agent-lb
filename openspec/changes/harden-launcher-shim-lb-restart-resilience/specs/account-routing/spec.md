## ADDED Requirements

### Requirement: Launcher shim absorbs transient LB restarts

The Claude launcher session shim MUST retry connection-level failures to agent-lb (connection refused or reset, such as the LB service restarting) with bounded exponential backoff before surfacing an error to the agent, so an in-flight request survives a brief restart. The shim MUST pass real HTTP responses straight through without retrying, including `429` and `503` responses that carry a `Retry-After` header. The shim MUST NOT retry read timeouts, because the request may already be in flight and re-sending could double-process it. The retry count and backoff MUST be bounded and tunable via environment variables, and the shim MUST stop retrying when the parent process has exited.

#### Scenario: Connection refused during an LB restart is retried

- **GIVEN** the launcher shim forwards a request while the LB service is restarting
- **AND** the connection is refused or reset before any response is produced
- **WHEN** the shim handles the failure
- **THEN** the shim retries with bounded backoff until the LB accepts the connection or the retry budget is exhausted
- **AND** a successful response after a retry is returned to the agent without surfacing the transient failure

#### Scenario: A real HTTP error response is not retried

- **GIVEN** the LB returns an HTTP response such as `429` or `503` with a `Retry-After` header
- **WHEN** the shim forwards that response
- **THEN** the shim passes the status and body straight through to the agent without retrying
