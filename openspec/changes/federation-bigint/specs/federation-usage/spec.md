## ADDED Requirements

### Requirement: Federation usage counters retain 64-bit totals

Federation daily request, session and token counters MUST use BIGINT storage. Upgrading an existing database MUST preserve its rollups.

#### Scenario: Daily totals exceed signed 32-bit range
- **GIVEN** an existing federation usage table upgraded to the latest schema
- **WHEN** a peer reports 3260172888 requests, sessions or tokens
- **THEN** ingestion succeeds and reading the stored rollup returns that exact value
