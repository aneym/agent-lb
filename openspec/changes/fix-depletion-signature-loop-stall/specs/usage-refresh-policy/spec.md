## ADDED Requirements

### Requirement: Depletion cache compares bounded history signatures
The depletion EWMA cache SHALL identify in-window history changes using the row count and the first and latest row edges, without hashing or rendering every row on a cache hit. Edge-visible corrections SHALL invalidate the cache. Interior-only in-place edits with unchanged count and edges are not detected by this signature.

#### Scenario: A newly recorded usage row changes depletion history
- **WHEN** a row is appended to the account's in-window usage history
- **THEN** the changed count or latest edge invalidates the cached depletion estimate
- **AND** checking the signature does not process every row's contents
