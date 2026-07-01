## ADDED Requirements

### Requirement: Permanent refresh failures leave the routing pool
The proxy account router SHALL treat provider-scoped or operation-scoped refresh
failure codes that wrap a known permanent refresh failure as the same canonical
permanent failure. Refresh error construction MUST set `is_permanent` for known
permanent refresh codes even when a caller supplies a false permanence flag. When
an Anthropic message request selects an account whose token refresh fails with a
permanent refresh failure before upstream I/O begins, the router MUST mark that
account non-routable until reauthentication and MUST attempt another eligible
account before returning the auth failure, when another eligible account remains.

#### Scenario: Prefixed invalid grant is classified as reauthentication-required
- **WHEN** account refresh fails with `auth_refresh_invalid_grant`
- **THEN** the system marks the account reauthentication-required using the
  canonical `invalid_grant` permanent failure reason
- **AND** subsequent selection excludes the account until it is reauthenticated

#### Scenario: Invalid grant with false wrapper flag is still permanent
- **WHEN** a refresh caller constructs a refresh error with `invalid_grant` and a
  false permanence flag
- **THEN** the refresh error reports itself as permanent
- **AND** account refresh handling marks the account reauthentication-required

#### Scenario: Anthropic refresh failure fails over before surfacing 401
- **GIVEN** an Anthropic messages request selects an eligible account whose refresh
  token fails with `auth_refresh_invalid_grant`
- **AND** another eligible Anthropic account remains
- **WHEN** the request is routed
- **THEN** the failed account is recorded as an auth error for that request
- **AND** the request is attempted with the remaining eligible Anthropic account
  before returning a downstream auth failure

### Requirement: Anthropic streaming proxy errors release reserved API-key usage
The proxy SHALL release reserved API-key usage when an Anthropic streaming
response later converts a proxy failure into an SSE error event, unless the
reservation has already been finalized or released.

#### Scenario: Streaming error event releases reservation
- **GIVEN** an Anthropic streaming request has an API-key usage reservation
- **WHEN** the stream body raises an Anthropic proxy error and the API emits an
  SSE error event
- **THEN** the API releases the usage reservation
