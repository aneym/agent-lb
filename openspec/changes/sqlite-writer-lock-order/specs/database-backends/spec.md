## ADDED Requirements

### Requirement: SQLite writers acquire their connection before the writer lock

For file-backed SQLite, the writer section SHALL accept the writing session and check out its connection before waiting for the process-wide writer lock. The helper SHALL NOT hold the writer lock while waiting for a pooled connection. Postgres and in-memory SQLite SHALL remain no-ops. Existing transactions, writes, and commits SHALL remain unchanged, and no writer-lock timeout SHALL be introduced.

#### Scenario: Mixed SQLite writers do not wedge a bounded pool

- **WHEN** four query-first writers and four fresh-session writers run concurrently against a file-backed SQLite pool with two connections, zero overflow, and a two-second pool timeout
- **THEN** all eight writers finish without a pool timeout within ten seconds
