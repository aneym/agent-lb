## ADDED Requirements

### Requirement: Usage summary log metrics avoid request-log hydration
The usage summary SHALL compute log-derived metrics and cost with account-filtered SQL aggregates for both provider-scoped and unscoped requests, without loading the entire request-log window into application memory. It MAY reuse aggregates for up to 60 seconds per provider, window and account set. Usage-window remaining percentages SHALL continue to derive from usage rows, not the log-metrics cache.

#### Scenario: Repeated dashboard usage polls
- **WHEN** a dashboard polls the same usage summary within the cache lifetime
- **THEN** the response reuses log-derived metrics and cost without hydrating request-log rows
- **AND** the remaining percentages still reflect the usage rows
