## MODIFIED Requirements

### Requirement: Limit warm-up persistence

The database SHALL persist global warm-up settings, per-account opt-in, warm-up attempt history, and request-log source metadata. The per-account opt-in column SHALL default to enabled for newly inserted accounts.

#### Scenario: Warm-up attempt is unique per reset

- **WHEN** an attempt is stored for an account, window, and reset timestamp
- **THEN** the database enforces uniqueness for that account/window/reset tuple

#### Scenario: Existing installs keep stored opt-in state

- **WHEN** an existing database is migrated
- **THEN** global warm-up remains disabled
- **AND** existing account rows keep their stored per-account opt-in value (no backfill)

#### Scenario: New account rows default to opted in

- **WHEN** an account row is inserted without an explicit `limit_warmup_enabled` value
- **THEN** the stored row has `limit_warmup_enabled = true`

#### Scenario: Warm-up request logs remain separable from user traffic

- **WHEN** a warm-up request is logged
- **THEN** the request log records a source value that allows account usage summaries to exclude internal warm-up traffic
