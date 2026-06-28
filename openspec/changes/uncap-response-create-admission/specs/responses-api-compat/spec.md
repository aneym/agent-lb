## MODIFIED Requirements

### Requirement: Responses account selection accounts for in-flight pressure

For Responses API requests, usage-based routing MUST include immediate
in-process account pressure in addition to persisted usage. Account selection
MUST account for in-flight response-create work, active streams, leased
token/cost estimates, recent selection pressure, account health, and enabled
account-local caps. Selection and lease acquisition MUST be atomic with respect
to other in-process selections, and the critical section MUST NOT perform
database calls, network calls, sleeps, or other blocking I/O. Active
response-create and stream leases MUST remain routing pressure signals when the
matching account-local cap is `0`.

#### Scenario: Concurrent burst spreads before upstream usage refreshes

- **GIVEN** multiple eligible accounts have similar persisted usage
- **WHEN** many `/v1/responses` requests arrive concurrently before upstream
  usage refreshes
- **THEN** selected accounts are distributed according to immediate in-flight
  pressure and enabled caps
- **AND** one account does not receive all requests solely because persisted
  usage was stale

#### Scenario: Uncapped response-create leases remain pressure

- **GIVEN** the account response-create cap is unset or configured as `0`
- **AND** in-flight response-create work exists on an eligible account
- **WHEN** a new `/v1/responses` request is routed
- **THEN** the account remains eligible for selection
- **AND** the in-flight response-create work still contributes to pressure
  scoring
