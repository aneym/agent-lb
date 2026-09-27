## ADDED Requirements

### Requirement: Request logs persist nullable attribution without rewriting history

`request_logs` SHALL have nullable `caller_user` (at most 32 characters),
`caller_user_source`, `caller_machine` (at most 48 characters), and
`caller_machine_source` columns. The migration MUST be an idempotent batch
migration, supporting upgrade and downgrade on SQLite and PostgreSQL, chained
onto the single current Alembic head. It MUST NOT backfill existing rows. It
MUST NOT add an attribution index unless query-plan evidence shows the bounded
usage summary needs one.

#### Scenario: Historical rows remain unknown

- **GIVEN** request logs written before the attribution migration
- **WHEN** the migration upgrades the database
- **THEN** the old rows remain present with all four attribution fields null
- **AND** new request logs can store all four fields

#### Scenario: Both database backends migrate cleanly

- **WHEN** the migration upgrades and downgrades disposable SQLite and PostgreSQL databases
- **THEN** each operation succeeds and Alembic has one head
