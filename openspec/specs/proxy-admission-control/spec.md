# proxy-admission-control Specification

## Purpose
Define how the proxy protects itself under load while preserving short request paths and surfacing local overload clearly.
## Requirements
### Requirement: Downstream proxy admission is split by traffic class

The system MUST enforce independent downstream admission limits for proxy HTTP requests, proxy websocket sessions, compact HTTP requests, and dashboard traffic. Exhausting one proxy lane MUST NOT consume capacity from the others.

#### Scenario: Websocket session load does not starve HTTP responses
- **WHEN** the proxy websocket admission lane is full
- **THEN** new websocket sessions are rejected locally
- **AND** eligible proxy HTTP requests may still proceed if their own lane has capacity

#### Scenario: Compact lane survives general proxy load
- **WHEN** the general proxy HTTP lane is saturated
- **AND** the compact lane still has capacity
- **THEN** `/backend-api/codex/responses/compact` and `/v1/responses/compact` requests continue to be admitted

### Requirement: Local overload responses are explicit

When the proxy rejects a request locally because an admission lane or expensive-work stage is full, it MUST return a local-overload response with a `Retry-After` header. HTTP requests MUST use an OpenAI-style error envelope and websocket handshake denials MUST use an HTTP denial response instead of a pre-accept close frame.

#### Scenario: HTTP admission rejection returns explicit overload envelope
- **WHEN** a proxy HTTP request is rejected locally for overload
- **THEN** the response status is `429`
- **AND** the response includes `Retry-After`
- **AND** the error payload identifies the failure as local proxy overload instead of upstream unavailability

#### Scenario: Websocket handshake rejection returns explicit overload status
- **WHEN** a websocket handshake is rejected locally for overload
- **THEN** the client receives an HTTP denial response with the real overload status
- **AND** the server access log reflects that overload status instead of `403 Forbidden`

### Requirement: Expensive upstream work is admission controlled

The proxy MUST enforce separate in-process admission limits for token refresh, upstream websocket connect, and first-turn response creation.

#### Scenario: Token refresh admission rejects excess work
- **WHEN** concurrent forced token refresh work reaches the configured refresh limit
- **THEN** additional refresh attempts are rejected locally with an explicit overload response

#### Scenario: Response creation admission releases after first upstream acceptance
- **WHEN** the proxy is waiting for an upstream response to be created
- **THEN** that request holds a response-create admission slot
- **AND** the slot is released when the request receives `response.created` or fails before creation completes

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
- **AND** response-create lease count alone does not produce `account_response_create_cap`

#### Scenario: Default stream pressure does not reject healthy accounts

- **GIVEN** the account stream cap is unset or configured as `0`
- **AND** active streams exist on every eligible account
- **WHEN** a new `/v1/responses` stream request is routed
- **THEN** active stream count may affect account pressure scoring
- **AND** active stream count alone does not produce `account_stream_cap`

#### Scenario: Configured account cap rejects saturated accounts

- **GIVEN** an account-local response-create or stream cap is configured to a nonzero value
- **AND** every eligible account is at that cap
- **WHEN** a new matching Responses request is routed
- **THEN** the proxy returns local overload with `account_response_create_cap` or `account_stream_cap`
- **AND** the response includes `Retry-After`

#### Scenario: Hard continuity owner saturation fails closed

- **GIVEN** a follow-up request requires a specific previous-response owner account
- **AND** that account is at its configured nonzero account stream or response-create cap
- **WHEN** no safe continuity-preserving alternative exists
- **THEN** the proxy returns a bounded local overload/continuity failure
- **AND** the failure reason is stable and low-cardinality

### Requirement: Local overload reasons are stable and distinguishable

Local Responses overload failures MUST expose stable low-cardinality reason fields in logs and metrics so operators can distinguish `bridge_queue_full`, `response_create_gate_timeout`, `hard_affinity_saturated`, `previous_response_owner_unavailable`, `global_admission_timeout`, `capacity_exhausted_active_sessions`, `account_response_create_cap`, and `account_stream_cap`. These local reasons MUST NOT be reported as upstream rate limits.
Account-local lease metrics MUST expose low-cardinality acquired, released, stale-reclaimed, active, and cap-rejection signals by lease kind so operators can see response-create and stream pressure even when the matching account-local cap is disabled.
When the Prometheus endpoint is enabled, the metrics server MUST bind to loopback by default unless an operator configures a different metrics host.

#### Scenario: Bridge queue saturation is not ambiguous

- **WHEN** a local HTTP bridge queue rejects a request
- **THEN** logs and metrics use the stable reason `bridge_queue_full`
- **AND** they do not use the ambiguous alias `queue_full`

#### Scenario: Account cap rejection is local overload

- **WHEN** every eligible account is unavailable because of account-local caps
- **THEN** the HTTP response is a local overload response with `Retry-After`
- **AND** logs and metrics identify `account_response_create_cap` or `account_stream_cap`

#### Scenario: Uncapped account pressure remains observable

- **GIVEN** the account response-create cap or account stream cap is configured as `0`
- **WHEN** matching account leases are acquired and released
- **THEN** low-cardinality metrics report active, acquired, and released lease pressure by kind
- **AND** no cap-rejection metric is emitted solely because the cap is disabled

#### Scenario: Metrics endpoint defaults to loopback

- **WHEN** the Prometheus metrics endpoint is enabled without an explicit host override
- **THEN** the metrics server binds to `127.0.0.1`
- **AND** operators MAY configure another metrics host for remote scraping

### Requirement: HTTP bridge startup admission waits are bounded

The proxy MUST apply the configured proxy admission wait timeout to HTTP bridge startup waits for per-session response-create gate acquisition, bridge capacity waiters, and in-flight session creation waiters. When the timeout expires, the proxy MUST reject the request locally with HTTP 429 and an OpenAI-style `proxy_overloaded` error envelope. Timing out while observing another request's pending in-flight session creation MUST evict that in-flight marker when it is still pending so later requests can attempt a fresh bridge session instead of waiting on the same stalled future.

If a request owns in-flight bridge session creation and is cancelled or fails after publishing the in-flight marker but before registering the created session, the proxy MUST remove or settle that in-flight marker. If a session owner later finishes creation after its in-flight marker was evicted, the owner MUST NOT return an unregistered bridge session to the caller.

#### Scenario: Per-session response-create gate does not open

- **WHEN** a bridged Responses request waits for a session response-create gate
- **AND** the gate does not open before the configured proxy admission wait timeout
- **THEN** the request is rejected locally with HTTP 429
- **AND** the error payload uses `error.code = "proxy_overloaded"`
- **AND** no response-create gate lease is recorded on that request state

#### Scenario: In-flight bridge session creation does not finish

- **WHEN** a bridged Responses request waits on another request's in-flight session creation
- **AND** the in-flight creation does not finish before the configured proxy admission wait timeout
- **THEN** the waiter is rejected locally with HTTP 429 and `error.code = "proxy_overloaded"`
- **AND** the stalled in-flight marker is evicted if it is still pending

#### Scenario: Bridge capacity waiter does not make progress

- **WHEN** the HTTP bridge is at capacity and a request waits for in-flight bridge work to free capacity
- **AND** no capacity becomes available before the configured proxy admission wait timeout
- **THEN** the waiter is rejected locally with HTTP 429 and `error.code = "proxy_overloaded"`

#### Scenario: In-flight owner is cancelled during stale session close

- **WHEN** a bridge session creation owner has published an in-flight marker
- **AND** it is cancelled while closing a stale local bridge session before creating the replacement session
- **THEN** the in-flight marker is removed or settled
- **AND** later requests do not remain blocked on that cancelled owner's future

### Requirement: Repeated identical compaction turns open a local circuit breaker

The proxy MUST fingerprint `/backend-api/codex/responses` requests whose input
contains a `compaction_trigger` item (fingerprint derived from the model and
the request input) and MUST reject further requests bearing the same
fingerprint with HTTP 429 and a `Retry-After` header once more than five such
requests have been admitted within a ten-minute sliding window. The rejection
MUST happen before an upstream account is selected or consumed. Requests with
distinct fingerprints, and non-compaction requests, MUST be unaffected. The
tracking state MUST be bounded in size and expire with the window. When the
breaker opens for a fingerprint, the proxy MUST record an audit event at most
once per window for that fingerprint.

#### Scenario: identical compaction turn repeats beyond the threshold

- **GIVEN** six identical compaction requests (same model and input) arriving
  within ten minutes
- **WHEN** the sixth request is admitted
- **THEN** it is rejected locally with HTTP 429 and a `Retry-After` header and
  no upstream account is consumed for it, and an audit event records the loop

#### Scenario: distinct compaction turns are not throttled

- **GIVEN** several compaction requests whose inputs differ
- **WHEN** they arrive within the same window
- **THEN** each is admitted normally

#### Scenario: the window expires and the breaker closes

- **GIVEN** a fingerprint that previously opened the breaker
- **WHEN** more than the window duration passes without that fingerprint
- **THEN** a new request with that fingerprint is admitted normally

#### Scenario: non-compaction traffic is never fingerprinted

- **GIVEN** a streaming request whose input has no `compaction_trigger` item
- **WHEN** it repeats identically many times within the window
- **THEN** the breaker never rejects it

### Requirement: Client-facing websockets have no pong deadline

The server MUST run its client-facing websocket protocol with transport pings
enabled and the pong deadline disabled (`ws_ping_interval=20.0`,
`ws_ping_timeout=None`), matching the upstream websocket leg's policy: the
service's own request and idle budgets decide when a turn has stalled, not the
transport pong watchdog. A connected agent client that blocks its event loop
longer than the ping interval MUST NOT be disconnected for a missed pong.

#### Scenario: Agent client stalls during long local work

- **GIVEN** a client websocket session with an in-flight turn
- **WHEN** the client does not answer transport pings for more than 20 seconds
- **THEN** the connection is not closed with 1011 "keepalive ping timeout"

