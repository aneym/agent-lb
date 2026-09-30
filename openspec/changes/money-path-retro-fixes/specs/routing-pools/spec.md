## ADDED Requirements

### Requirement: Budget burn reaches routing unchanged
PoolSummary SHALL serialize the daily burn field as burn24hPercent. Route SHALL consume it as the burn_24h pace basis.

#### Scenario: Cursor will empty before reset
- **WHEN** a serialized Cursor budget pool has ten percent remaining, twenty-four percent daily burn and one hundred hours until reset
- **THEN** route reports low with basis burn_24h and ten hours until empty

### Requirement: Usage corruption does not remove available seats
The seat recorder SHALL coerce non-numeric token values to zero while retaining the run. Pool readers SHALL retain account availability with empty usage when daily or run usage validation fails.

#### Scenario: An authenticated account has malformed usage
- **WHEN** daily or run token values are malformed
- **THEN** the account remains eligible and its usage is empty

### Requirement: Pace defaults do not alter persisted policy
Route SHALL apply defaults at pace evaluation, treating malformed pace objects and non-numeric thresholds as missing. Learn apply SHALL preserve all source-table fields except overrides.

#### Scenario: Learning writes a demotion
- **WHEN** learn applies an override to a table lacking pace defaults
- **THEN** no pace default is persisted and other source fields remain unchanged

### Requirement: Cursor menubar budget rows use budget pools
The Cursor scope SHALL render budget lines only for cli_seat_budget pools.

#### Scenario: Availability and budget pools coexist
- **WHEN** Cursor availability and budget pools are present
- **THEN** the availability pool does not produce an Other models spending line
