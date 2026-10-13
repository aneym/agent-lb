## ADDED Requirements

### Requirement: Frame database requests use selected OAuth credentials
The proxy SHALL accept GET, POST, PUT, PATCH, and DELETE on `/api/frame/**` under the existing proxy authentication policy. It SHALL forward the method, encoded path and query, and request body to the configured Anthropic upstream, defaulting to `https://api.anthropic.com`, with the selected account's fresh OAuth bearer. Response status and body SHALL pass through unchanged. Session headers SHALL use the proxy's existing sticky key format.

#### Scenario: Watcher reads shared database
- **WHEN** a watcher sends GET `/api/frame/db/agent?artifact=a` with `x-claude-session-id` and `x-agent-lb-seat`
- **THEN** the proxy forwards it with the selected account's bearer and preserves the response body
- **AND** the request log records kind `frame` and the watcher seat

### Requirement: Frame logs identify caller and request kind
The proxy SHALL log frame requests with kind `frame`, the caller seat, and request identity through the existing request logger.

#### Scenario: Frame request completes
- **WHEN** a frame request completes with a response or upstream failure
- **THEN** the request log records kind `frame`, the selected account, and caller attribution

### Requirement: Frame authentication retry stays on one account
On HTTP 401 the proxy SHALL recover the rejected access token using the existing refresh helper and retry once on the same account. It SHALL return a repeated 401 unchanged and SHALL NOT fail over a frame request to another account or disable credentials solely on access-token rejection. Other upstream errors SHALL pass through without retry.

#### Scenario: Rejected bearer recovers
- **WHEN** the first frame response is 401 and refresh succeeds
- **THEN** exactly one retry uses the refreshed bearer on the selected account

#### Scenario: Refreshed bearer remains rejected
- **WHEN** the retry also returns 401
- **THEN** the proxy returns that response and logs the error without another refresh or account switch
