## ADDED Requirements

### Requirement: Cursor monthly spend accounting
The seat CLI SHALL record model and usage from leased releases and direct runs into UTC daily buckets, retaining 62 days, and SHALL allow registry tier and cycle day settings.

#### Scenario: Leased usage supplies monthly spend
- **WHEN** a Cursor lease is released with a model and readable usage file
- **THEN** its input, output and cached tokens are recorded exactly once in its daily model bucket

### Requirement: Configured Cursor budget pools
The pools service SHALL append first-match configured Cursor monthly budget pools without changing availability pools. It SHALL calculate tier budgets, observed spend, remaining, pace, last-day burn and cycle reset. Unbudgeted accounts SHALL contribute spend but not budgeted remaining figures. Malformed configuration entries SHALL be skipped.

#### Scenario: UTC accounting on a non-UTC host
- **WHEN** the service supplies a naive UTC timestamp on an Asia/Tokyo host
- **THEN** cycle spend and last-day burn match the same input on a UTC host

### Requirement: Optional budget response contract
PoolSummary SHALL expose optional camelCase budget fields in its serialization JSON schema and SHALL omit unset budget fields from existing non-budget pool responses.

#### Scenario: Clients discover budget fields
- **WHEN** a client reads the serialization schema
- **THEN** monthly spend, budget, remaining, pace, burn, reset and unbudgeted-account properties are present
