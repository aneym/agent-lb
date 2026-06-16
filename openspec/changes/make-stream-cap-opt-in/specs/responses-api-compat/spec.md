## MODIFIED Requirements

### Requirement: Responses account selection accounts for in-flight pressure

For Responses API requests, usage-based routing MUST include immediate
in-process account pressure in addition to persisted usage. Account selection
MUST account for in-flight response-create work, active streams, leased
token/cost estimates, recent selection pressure, account health, and enabled
account-local caps. Selection and lease acquisition MUST be atomic with respect
to other in-process selections, and the critical section MUST NOT perform
database calls, network calls, sleeps, or other blocking I/O. Active streams
MUST remain routing pressure signals even when the configured account stream cap
is `0`.

#### Scenario: Concurrent burst spreads before upstream usage refreshes

- **GIVEN** multiple eligible accounts have similar persisted usage
- **WHEN** many `/v1/responses` requests arrive concurrently before upstream
  usage refreshes
- **THEN** selected accounts are distributed according to immediate in-flight
  pressure and enabled caps
- **AND** one account does not receive all requests solely because persisted
  usage was stale

#### Scenario: File-pinned bridge request does not reroute under local pressure

- **GIVEN** an HTTP bridge `/v1/responses` request references an
  `input_file.file_id` pinned to an upstream account
- **AND** that owner account or bridge session rejects admission with local
  pressure before output starts
- **WHEN** the proxy handles the admission failure
- **THEN** it returns the owner account overload instead of soft-rerouting the
  payload to another account
- **AND** the file-scoped request is not replayed to an account that does not
  own the file

#### Scenario: Runtime lock excludes blocking I/O

- **WHEN** account selection holds the balancer runtime lock
- **THEN** the implementation performs only in-memory scoring and lease mutation
- **AND** database, network, sleep, or bridge queue waits happen outside that lock

### Requirement: Account leases release on all terminal paths

Every account-local lease acquired for a Responses request MUST be idempotently
released or settled on success, upstream error, local startup error, bridge
submit failure, startup probe conversion, non-streaming collect completion,
failover, downstream disconnect, cancellation, timeout, and retry. A bounded
stale-lease watchdog MUST reclaim leases that survive unexpected task
cancellation or exceptions, and stale reclamation MUST emit warning/metric
evidence. Leases MUST NOT be persisted to the database.

#### Scenario: Lease releases after downstream disconnect

- **WHEN** a streaming `/v1/responses` client disconnects before a terminal
  upstream event
- **THEN** the account stream lease is released exactly once
- **AND** later routing pressure no longer includes that stream

#### Scenario: WebSocket local account cap releases API-key reservation

- **GIVEN** a WebSocket `response.create` has reserved API-key usage
- **AND** account-local response-create lease acquisition fails with
  `account_response_create_cap`
- **WHEN** the proxy emits the local terminal failure
- **THEN** the API-key usage reservation is released
- **AND** the pending request is removed from websocket local state

#### Scenario: Stale watchdog recovers orphaned lease

- **WHEN** a request task exits unexpectedly after acquiring an account lease
- **AND** the lease exceeds the configured TTL
- **THEN** the watchdog releases the stale lease
- **AND** emits a low-cardinality warning/metric

#### Scenario: Configured active stream cap is not bypassed by age alone

- **GIVEN** the account stream cap is configured to a nonzero value
- **AND** a stream lease is older than the base lease TTL
- **AND** the configured Responses stream or HTTP bridge request budget has not
  elapsed
- **WHEN** account lease stale reclamation runs
- **THEN** the stream lease still counts against account-local stream pressure
- **AND** the proxy does not admit extra streams over the configured account
  stream cap by age alone
