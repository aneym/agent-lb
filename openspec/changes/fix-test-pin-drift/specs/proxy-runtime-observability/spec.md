## MODIFIED Requirements

### Requirement: Proxy 4xx/5xx responses are logged with error detail

When the proxy returns a 4xx or 5xx response for a proxied request, the system MUST log the request id, method, path, status code, error code, and error message to the console. The error message MUST pass through secret redaction before it is logged: keyed secrets (password/token/api-key style pairs), bearer tokens, and authorization values are replaced with a redaction marker, and over-long values are truncated. For local admission rejections, the log MUST also include the rejection stage or lane.

#### Scenario: Local admission rejection is logged

- **WHEN** the proxy rejects a request locally because a downstream or expensive-work admission lane is full
- **THEN** the console log includes the local response status, normalized error code and message
- **AND** it includes which admission lane or stage rejected the request

#### Scenario: Secrets in upstream error messages never reach the log

- **GIVEN** an upstream error message that embeds a bearer token
- **WHEN** the proxy logs the 4xx/5xx response
- **THEN** the log line carries the error code and a redacted message
- **AND** the raw token value does not appear anywhere in the log output
