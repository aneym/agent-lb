## MODIFIED Requirements

### Requirement: API Key authentication global switch

The system SHALL provide an `api_key_auth_enabled` boolean in
`DashboardSettings`. When false (default), local requests to protected proxy
routes MAY proceed without an API key. Operators MAY additionally opt specific
non-local proxy clients into unauthenticated access by configuring
`proxy_unauthenticated_client_cidrs`. The explicit allowlist SHALL be evaluated
against the resolved client IP used by firewall locality checks: forwarded
client headers MAY influence that IP only when proxy header trust is enabled and
the socket source belongs to configured trusted proxy CIDRs. Requests that are
neither local nor explicitly allowlisted MUST be rejected until proxy
authentication is configured. When true, protected proxy routes require a valid
API key in the `Authorization` header using the Bearer authentication scheme.

#### Scenario: Disable API key auth for an explicitly allowlisted direct proxy client

- **WHEN** admin submits `PUT /api/settings` with `{ "apiKeyAuthEnabled": false }`
- **AND** proxy header trust is disabled
- **AND** the request socket peer IP belongs to configured
  `proxy_unauthenticated_client_cidrs`
- **THEN** the protected proxy route proceeds without API key authentication

#### Scenario: Trusted proxy headers do not turn loopback into a public bypass

- **WHEN** admin submits `PUT /api/settings` with `{ "apiKeyAuthEnabled": false }`
- **AND** `firewall_trust_proxy_headers=true`
- **AND** the request socket peer IP is in `firewall_trusted_proxy_cidrs`
- **AND** the request includes an `X-Forwarded-For` public client IP
- **AND** only the loopback socket peer range is configured in
  `proxy_unauthenticated_client_cidrs`
- **THEN** the protected proxy route rejects the request with 401 until proxy
  authentication is configured

#### Scenario: Trusted proxy headers can allow an explicit resolved client CIDR

- **WHEN** admin submits `PUT /api/settings` with `{ "apiKeyAuthEnabled": false }`
- **AND** `firewall_trust_proxy_headers=true`
- **AND** the request socket peer IP is in `firewall_trusted_proxy_cidrs`
- **AND** the resolved forwarded client IP belongs to configured
  `proxy_unauthenticated_client_cidrs`
- **THEN** the protected proxy route proceeds without API key authentication

#### Scenario: Spoofed forwarded headers from untrusted peers do not satisfy the allowlist

- **WHEN** admin submits `PUT /api/settings` with `{ "apiKeyAuthEnabled": false }`
- **AND** a request from an untrusted socket peer includes `X-Forwarded-For:
  127.0.0.1`
- **AND** `127.0.0.1/32` is configured in
  `proxy_unauthenticated_client_cidrs`
- **THEN** the dependency ignores the forwarded header
- **AND** the protected proxy route rejects the request with 401 until proxy
  authentication is configured
