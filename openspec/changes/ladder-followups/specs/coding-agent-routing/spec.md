## ADDED Requirements

### Requirement: Rollback inspection validates inactive ladders

Route seats SHALL validate every configured ladder with the same validator used by pick, menu and seat. Malformed inactive ladders SHALL make seats exit with code 3 and a route diagnostic without a traceback. Pick, menu and seat SHALL validate only the active ladder, preserving baseline rollback when an inactive ladder is broken.

#### Scenario: Inactive verification reference is broken

- **WHEN** baseline is active and an interim verification reference names a nonexistent rung
- **THEN** `route seats` exits 3 with a route diagnostic and no traceback

### Requirement: Cursor closest hints exclude retired models

Closest-model hints SHALL exclude models retired by route's existing rule. Cursor preflight and failover changes (item 3) are out of scope.

#### Scenario: Only retired nearby models are listed

- **WHEN** no candidate lists the requested model and nearby listed ids are retired
- **THEN** seat refuses dispatch without suggesting the retired ids

## Scope Cuts

- REMOVED item 3: Cursor preflight early stop and lazy failover probing are out of scope.
- REMOVED item 4: Composer-first exploration is out of scope; no exploration-order change is included.

- REMOVED item 5: pool refill inspection is split into the pool-refills change.
