## ADDED Requirements

### Requirement: Depletion cache signatures use history edges

The depletion EWMA cache SHALL identify an account, limit, and window's history by its row count and first and latest row signatures. Each edge signature SHALL include row id when available, recorded time, used percentage, reset time, and window minutes. Signature comparison SHALL require constant work with respect to history length and MUST NOT hash or format every row. Filtering or materializing the history MAY still require a linear pass.

When the signature is unchanged, depletion calculation SHALL reuse the cached EWMA state while recalculating time-dependent metrics. A changed row count or edge signature SHALL rebuild the EWMA state. Interior-only corrections that preserve row count and both edges SHALL NOT be required to invalidate the cache.

#### Scenario: Repeated dashboard poll reuses EWMA state

- **GIVEN** an account, limit, and window has a cached EWMA state
- **WHEN** another calculation uses history with the same row count and edge signatures
- **THEN** the cached state is reused without replaying all history rows
- **AND** time-dependent risk and exhaustion metrics use the current calculation time

#### Scenario: History membership changes

- **WHEN** a new tail row is appended or a leading row leaves the window
- **THEN** the changed signature causes an EWMA rebuild

#### Scenario: An edge value is corrected

- **WHEN** a first or latest row's signed value changes even though its id and the row count stay unchanged
- **THEN** the signature changes and the EWMA state is rebuilt

#### Scenario: Interior-only correction preserves the cache key

- **WHEN** only interior history values change and row count and both edge signatures remain identical
- **THEN** the edge signature remains unchanged
- **AND** the cache may reuse its existing EWMA state
