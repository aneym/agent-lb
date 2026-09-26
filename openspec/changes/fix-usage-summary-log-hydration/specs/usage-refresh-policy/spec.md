## ADDED Requirements

### Requirement: Usage summaries aggregate request logs without hydrating the window

The usage summary SHALL compute request-log-derived metrics and costs through account-filtered SQL aggregates for both provider-scoped and unscoped requests. It MUST NOT hydrate the full window of request-log ORM rows to derive these summaries. Aggregated results SHALL preserve the existing request count, token, error, and cost semantics.

Provider-scoped summaries SHALL include only the selected provider's eligible accounts and SHALL exclude unattributed logs. Unscoped summaries SHALL include the eligible account set and unattributed logs.

#### Scenario: Scoped summary excludes another provider's traffic

- **GIVEN** logs exist for accounts from multiple providers and for unattributed requests
- **WHEN** a usage summary is requested for one provider
- **THEN** SQL aggregation includes only that provider's eligible account ids
- **AND** it excludes logs for other accounts and unattributed requests

#### Scenario: Unscoped summary includes unattributed traffic

- **WHEN** a usage summary is requested without a provider filter
- **THEN** SQL aggregation includes logs for the eligible account set and unattributed requests
- **AND** metrics and costs match the corresponding filtered request-log calculation

### Requirement: Log-derived usage aggregates have a short-lived cache

The service SHALL cache completed log-derived metrics and cost aggregates in process for 60 seconds, keyed by provider, window duration, and the account-id set. Cache age SHALL use monotonic time. A poll with an unexpired matching entry SHALL reuse it; an expired or absent entry SHALL trigger SQL recomputation. Current usage-window percentages SHALL continue to derive from usage rows rather than this request-log cache.

#### Scenario: Repeated poll reuses a completed aggregate

- **GIVEN** the same provider, window duration, and account-id set has a cached aggregate younger than 60 seconds
- **WHEN** a usage summary is requested
- **THEN** the service reuses the cached log-derived metrics and costs
- **AND** it obtains usage-window percentages from usage rows

#### Scenario: Expiry or account-set change requires recomputation

- **WHEN** the aggregate cache entry has expired or the requested account-id set differs
- **THEN** the service recomputes log-derived metrics and costs using SQL aggregates
- **AND** it stores the result under the requested cache key
