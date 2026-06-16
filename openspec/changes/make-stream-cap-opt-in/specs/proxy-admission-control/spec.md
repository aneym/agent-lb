## MODIFIED Requirements

### Requirement: Account-local Responses work is capped before upstream creation

Proxy Responses admission MUST enforce account-local response-create concurrency
limits for `/v1/responses`, `/backend-api/codex/responses`, and compact Responses
traffic in addition to process-wide admission limits. The default account
response-create cap MUST be 4 unless operators configure a different value. The
default account stream cap MUST be 0, meaning active stream count does not reject
new work by default. When operators configure a nonzero account stream cap, the
proxy MUST enforce that cap before upstream creation. When an account is at an
enabled account-local cap, new soft-affinity work MUST prefer another eligible
account before returning local overload. Hard-continuity work MAY fail closed
when the required owner account is saturated by an enabled cap.

#### Scenario: Soft work avoids saturated account

- **GIVEN** account A is at its account response-create cap
- **AND** account B is eligible and below cap
- **WHEN** a soft-affinity `/v1/responses` request is routed
- **THEN** the proxy selects account B instead of queueing on account A

#### Scenario: Default stream pressure does not reject healthy accounts

- **GIVEN** the account stream cap is unset or configured as `0`
- **AND** active streams exist on every eligible account
- **WHEN** a new `/v1/responses` stream request is routed
- **THEN** active stream count may affect account pressure scoring
- **AND** active stream count alone does not produce `account_stream_cap`

#### Scenario: Configured stream cap rejects saturated accounts

- **GIVEN** the account stream cap is configured to a nonzero value
- **AND** every eligible account is at that stream cap
- **WHEN** a new stream request is routed
- **THEN** the proxy returns local overload with `account_stream_cap`
- **AND** the response includes `Retry-After`
