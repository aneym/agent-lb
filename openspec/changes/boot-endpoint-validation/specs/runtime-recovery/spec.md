## ADDED Requirements

### Requirement: Endpoint validation precedes formatting
The shared database endpoint parser SHALL reject an empty hostname, hostname characters outside ASCII letters, digits, underscore, dot, colon, percent and hyphen, and invalid numeric ports before formatting or shell splitting. Failure SHALL emit only `unparseable` and return non-zero.

#### Scenario: Whitespace in the hostname
- **WHEN** a configured database hostname contains a space followed by credential-like content
- **THEN** parsing fails without emitting any hostname or credential-like content

#### Scenario: Immediately ready database
- **WHEN** the first TCP readiness probe succeeds without waiting
- **THEN** the runtime starts without requiring an endpoint log line, and no credentials appear in its logs
