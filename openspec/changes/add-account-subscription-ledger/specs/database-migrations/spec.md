## ADDED Requirements

### Requirement: Account subscription ledger persistence

The database SHALL persist nullable local subscription ledger fields on accounts without requiring historical backfill values.

#### Scenario: Subscription ledger columns exist after migration

- **WHEN** migrations run to head
- **THEN** `accounts` contains nullable columns for subscription status, next charge timestamp, active-until timestamp, amount, currency, last verified timestamp, and notes
