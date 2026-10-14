## ADDED Requirements

### Requirement: Bounded SQLite latest lookup
SQLite latest additional quota lookups SHALL perform indexed seeks per account and matching alias, preserving recorded_at, used_percent, id descending precedence and account/window/since filtering.

#### Scenario: Historical aliases and ties
- **WHEN** canonical and legacy aliases contain matching rows
- **THEN** the latest row SHALL match the existing ranking semantics
