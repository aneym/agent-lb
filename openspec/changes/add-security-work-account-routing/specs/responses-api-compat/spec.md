## ADDED Requirements

### Requirement: Security-work authorization errors can route to authorized accounts

When an upstream Responses request fails because the work requires cybersecurity authorization, agent-lb MUST retry the request on an account marked as security-work-authorized when the request can be safely replayed on a different account. The retry MUST exclude the account that produced the authorization error.

#### Scenario: Unpinned stream request retries on an authorized account

- **WHEN** an unpinned streamed Responses request fails with a security-work authorization error on an account that is not security-work-authorized
- **AND** at least one eligible security-work-authorized account is available
- **THEN** agent-lb emits a non-terminal `agent_lb.warning` with `code="security_work_authorization_required"` and `action="retry_security_work_authorized"`
- **AND** agent-lb retries the request with account selection restricted to security-work-authorized accounts

#### Scenario: No authorized account is available

- **WHEN** agent-lb attempts a security-work-authorized retry
- **AND** no security-work-authorized accounts are available
- **THEN** agent-lb emits a non-terminal `agent_lb.warning` with `code="no_security_work_authorized_accounts"`
- **AND** agent-lb either continues normal account failover when safe or returns the original security-work authorization error when normal failover is exhausted or unsafe

#### Scenario: Pinned requests are not moved to another account

- **WHEN** a security-work authorization error occurs for a request pinned by file ownership or previous-response ownership
- **THEN** agent-lb MUST NOT replay the request on a different account
- **AND** the client receives the original security-work authorization failure.

#### Scenario: WebSocket replay releases the response-create gate

- **WHEN** a downstream websocket request is eligible for security-work replay
- **THEN** agent-lb releases the request's response-create gate before scheduling the replay
- **AND** the replay can acquire the gate instead of blocking behind the failed first attempt
