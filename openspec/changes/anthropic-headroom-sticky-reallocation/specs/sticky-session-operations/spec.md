## ADDED Requirements

### Requirement: Rate-limit failover re-pins the session where the cache was rebuilt

The durable session mapping MUST point at the account that served the successful
response after an in-flight Anthropic request fails over from an upstream rate
limit — so the next request reuses the
prompt cache the failover just wrote. The session MUST NOT bounce back to the
cooled-down account when its window resets.

#### Scenario: Failover account becomes the new durable pin

- **GIVEN** an Anthropic session pinned to account A
- **AND** account A returns an upstream 429 with a reset time
- **WHEN** the request transparently fails over and account B serves the response
- **THEN** the session's durable mapping points at account B
- **AND** the next request routes to account B without re-selection churn
