## ADDED Requirements

### Requirement: Cursor Claude family aliases
The router SHALL resolve effort-qualified `sonnet-latest` and `opus-latest` aliases to the newest served Cursor Claude id with the requested effort, excluding fast variants. It SHALL memoize Cursor discovery per process, including empty results. A resolution chain SHALL return the first resolvable name; only when no name resolves SHALL undefined latest aliases exit 3 with `route: unknown alias <names>`.

#### Scenario: Resolve an effort-qualified family
- **WHEN** `route resolve sonnet-latest-high` sees `claude-sonnet-5-5-high` in Cursor's model list
- **THEN** it returns `claude-sonnet-5-5-high`

#### Scenario: Unknown latest alias
- **WHEN** `route resolve foo-latest` is invoked and the table has no such alias
- **THEN** it exits 3 and identifies the unknown alias on stderr

### Requirement: Cursor dispatch model preflight
The seat SHALL discover Cursor models under each candidate account's environment and cache results per account id per process. It SHALL skip accounts whose non-empty lists lack the resolved id and reject only when no candidate can run it, before spawning a task. A pinned account SHALL be the sole candidate. The error SHALL show up to three available ids of the same family. Empty or failed discovery SHALL preserve dispatch.

#### Scenario: Unsupported model
- **WHEN** Cursor lists models but not the selected id
- **THEN** the seat fails with `<id> is not a Cursor model; closest: <ids>` without spawning a task

#### Scenario: Discovery unavailable
- **WHEN** Cursor model discovery fails or returns an empty list
- **THEN** the seat still attempts the selected model

### Requirement: Superseded Claude warning
The seat SHALL warn that a selected `claude-opus-5-*` or `claude-sonnet-5-*` id, excluding generation `5-5`, is superseded by the newest same-family, same-effort id. A warning SHALL NOT deny an intentionally chosen model that Cursor serves.

#### Scenario: Older served generation
- **WHEN** a served `claude-opus-5-thinking-high` model is chosen and `claude-opus-5-5-high` is available
- **THEN** stderr warns `superseded by claude-opus-5-5-high` and the task runs
