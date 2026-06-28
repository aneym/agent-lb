## MODIFIED Requirements

### Requirement: Account-local Responses work is capped before upstream creation

For `/v1/responses`, `/backend-api/codex/responses`, and compact Responses traffic, the proxy MUST support account-local response-create and streaming concurrency limits in addition to process-wide admission limits. The default account response-create cap MUST be `0` and the default account stream cap MUST be `0`, meaning account-local response-create and stream counts do not reject new work by default. When operators configure a nonzero account-local cap, the proxy MUST enforce that cap before upstream creation. When an account is at an enabled cap, new soft-affinity work MUST prefer another eligible account before returning local overload. Hard-continuity work MAY fail closed when the required owner account is saturated by an enabled cap.

#### Scenario: Soft work avoids saturated account

- **GIVEN** account A is at its configured nonzero account response-create cap
- **AND** account B is eligible and below cap
- **WHEN** a soft-affinity `/v1/responses` request is routed
- **THEN** the proxy selects account B instead of queueing on account A

#### Scenario: Default response-create pressure does not reject healthy accounts

- **GIVEN** the account response-create cap is unset or configured as `0`
- **AND** response-create leases exist on every eligible account
- **WHEN** a new `/v1/responses` request is routed
- **THEN** response-create lease count may affect account pressure scoring
- **AND** response-create lease count alone does not produce
  `account_response_create_cap`

#### Scenario: Default stream pressure does not reject healthy accounts

- **GIVEN** the account stream cap is unset or configured as `0`
- **AND** active streams exist on every eligible account
- **WHEN** a new `/v1/responses` stream request is routed
- **THEN** active stream count may affect account pressure scoring
- **AND** active stream count alone does not produce `account_stream_cap`

#### Scenario: Configured account cap rejects saturated accounts

- **GIVEN** an account-local response-create or stream cap is configured to a
  nonzero value
- **AND** every eligible account is at that cap
- **WHEN** a new matching Responses request is routed
- **THEN** the proxy returns local overload with `account_response_create_cap` or
  `account_stream_cap`
- **AND** the response includes `Retry-After`

#### Scenario: Hard continuity owner saturation fails closed

- **GIVEN** a follow-up request requires a specific previous-response owner
  account
- **AND** that account is at its configured nonzero account stream or
  response-create cap
- **WHEN** no safe continuity-preserving alternative exists
- **THEN** the proxy returns a bounded local overload/continuity failure
- **AND** the failure reason is stable and low-cardinality

### Requirement: Local overload reasons are stable and distinguishable

Local Responses overload failures MUST expose stable low-cardinality reason fields in logs and metrics so operators can distinguish `bridge_queue_full`, `response_create_gate_timeout`, `hard_affinity_saturated`, `previous_response_owner_unavailable`, `global_admission_timeout`, `capacity_exhausted_active_sessions`, `account_response_create_cap`, and `account_stream_cap`. These local reasons MUST NOT be reported as upstream rate limits.
Account-local lease metrics MUST expose low-cardinality acquired, released, stale-reclaimed, active, and cap-rejection signals by lease kind so operators can see response-create and stream pressure even when the matching account-local cap is disabled.
When the Prometheus endpoint is enabled, the metrics server MUST bind to loopback by default unless an operator configures a different metrics host.

#### Scenario: Uncapped account pressure remains observable

- **GIVEN** the account response-create cap or account stream cap is configured as `0`
- **WHEN** matching account leases are acquired and released
- **THEN** low-cardinality metrics report active, acquired, and released lease pressure by kind
- **AND** no cap-rejection metric is emitted solely because the cap is disabled

#### Scenario: Metrics endpoint defaults to loopback

- **WHEN** the Prometheus metrics endpoint is enabled without an explicit host override
- **THEN** the metrics server binds to `127.0.0.1`
- **AND** operators MAY configure another metrics host for remote scraping
