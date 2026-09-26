## ADDED Requirements

### Requirement: Migration compatibility without history rewriting

Migration bootstrap SHALL recognize complete pre-existing schema created from current models without attempting to recreate an existing reset-credit table. Incomplete schema SHALL NOT be silently treated as complete. Drift checks SHALL distinguish actual index changes from equivalent reflected definitions. Existing migration files SHALL remain unchanged.

#### Scenario: A metadata-created database is migrated

- **WHEN** startup migrations inspect a database whose current model tables already exist
- **THEN** migration compatibility handling SHALL preserve its rows and advance its revision without duplicate-table errors
- **AND** actual missing or incompatible schema SHALL remain detectable

### Requirement: Immediate quota-policy visibility

After a successful settings update changes additional-quota routing policy, subsequent account-list responses in that process SHALL reflect the new policy without waiting for the old response cache to expire.

#### Scenario: An operator changes a quota policy

- **WHEN** a cached account list says a quota uses `burn_first` and an operator updates that quota to `preserve`
- **THEN** the next account-list response SHALL report `preserve`

### Requirement: Release snapshot scope

The June public-release candidate snapshot check SHALL enforce the recorded pending PR-head tasks and their evidence boundaries on the two named release changes. Other active feature changes SHALL be allowed to retain honest incomplete tasks. Every active change SHALL still carry a normative spec delta.

#### Scenario: An unrelated change has unfinished work

- **WHEN** a new feature records an incomplete task outside the two release changes
- **THEN** that task SHALL NOT be mistaken for a missing June release proof
- **AND** the release changes' exact PR-head evidence assertions SHALL still run
