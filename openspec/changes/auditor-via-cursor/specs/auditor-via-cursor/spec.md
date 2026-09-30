## ADDED Requirements

### Requirement: Alternate auditor pool routing
When a declared auditor pool is exhausted or critical, the class candidate walk SHALL consult optional `policy.auditor_via`, a map from the primary pool id to an alternate route containing string `pool`, `vendor`, and `model` values. If the alternate pool is not exhausted or critical, the seat SHALL remain eligible and its audit metadata SHALL contain the alternate model, pool, vendor as `via`, and a reason naming both pools. Existing seat availability and other eligibility checks SHALL remain in effect.

#### Scenario: Critical Claude pool with healthy Cursor pool
- **WHEN** the mechanical class's first non-Claude seat has a Claude auditor pool with one eligible account and `cursor-other` is healthy
- **THEN** the seat remains first
- **AND** its audit metadata contains `via: cursor`, `pool: cursor-other`, and `model: claude-sonnet-5-5-high`

#### Scenario: Primary pool exhausted
- **WHEN** the primary auditor pool has no eligible accounts and its configured alternate is healthy
- **THEN** the seat remains eligible with alternate audit metadata

### Requirement: Unavailable or malformed alternates preserve skips
The class candidate walk SHALL preserve the primary auditor pool's existing skip reason when no valid alternate is configured or the alternate pool is exhausted or critical. Malformed configuration SHALL NOT raise an exception.

#### Scenario: Alternate unavailable
- **WHEN** Claude is critical and Cursor is exhausted or critical
- **THEN** affected non-Claude seats retain the primary critical-pool skip reason

#### Scenario: Malformed alternate
- **WHEN** the alternate map or route fields have invalid types or required fields are missing
- **THEN** routing ignores the alternate and preserves existing skip behavior

### Requirement: Text audit output names alternate transport
The text pick audit line SHALL include `via cursor` when a Cursor alternate auditor is selected.

#### Scenario: Alternate shown to caller
- **WHEN** a text mechanical pick uses the configured Cursor auditor alternate
- **THEN** its audit line names `via cursor`
