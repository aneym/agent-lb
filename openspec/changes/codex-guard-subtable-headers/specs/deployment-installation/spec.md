## ADDED Requirements

### Requirement: Codex routing guard respects managed values stored in sub-tables

When a managed provider key is not among the selected provider table's own lines, the routing guard MUST compare the parsed document's value with the expected value. A matching value, whether set through a sub-table such as `[model_providers.<id>.env_http_headers]` or a dotted key, MUST be left unchanged and MUST NOT cause an insertion. A different value set outside those lines MUST fail with a guard error before any write. The guard MUST insert a managed key only when the parsed document has no value for it.

#### Scenario: Seat header stored as a sub-table

- **GIVEN** `[model_providers.agent-lb.env_http_headers]` sets `x-agent-lb-seat = "AGENT_LB_SEAT"`
- **WHEN** the routing guard runs
- **THEN** the file is unchanged and still parses as TOML

#### Scenario: Conflicting sub-table value

- **GIVEN** the sub-table sets a different header value
- **WHEN** the routing guard runs
- **THEN** it fails with a guard error and the file is unchanged
