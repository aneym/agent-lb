## ADDED Requirements
### Requirement: Anthropic blocked account exclusion
The selector SHALL exclude canceled subscriptions, paused accounts, and Anthropic accounts with exhausted weekly quota and a future reset, including sticky selections. A blocked pool SHALL return a local 429 without an upstream request.

#### Scenario: Sticky weekly exhaustion
- **WHEN** a session is pinned to an Anthropic account with exhausted weekly quota and a future reset
- **THEN** selection SHALL choose another eligible account or return a local 429

### Requirement: Organization OAuth denial recovery
An Anthropic 403 containing "OAuth authentication is currently not allowed for this organization" SHALL persist a subscription usability block and retry through existing account failover. A successful usage refresh SHALL clear only this block. Generic 403 responses SHALL preserve existing behavior.

#### Scenario: Organization rejects OAuth
- **WHEN** an eligible account receives the organization OAuth denial
- **THEN** the request SHALL retry on another eligible account and subsequent requests SHALL exclude the rejected account until successful refresh
