### Requirement: Claude Fable pricing is recognized

The system MUST recognize Claude Fable 5 pricing when computing request costs. Aliases for the same model family, including suffixed model identifiers such as `claude-fable-5[1m]`, MUST resolve to the canonical `claude-fable-5` price table entry.

#### Scenario: Fable request logs receive cost accounting

- **WHEN** a Claude request log is recorded with model `claude-fable-5`
- **THEN** the system computes `cost_usd` from input, output, cache creation, and cache read token counts
- **AND** dashboard, account, report, and API-key cost rollups include that cost through the persisted request-log value

#### Scenario: Fable model aliases resolve to canonical pricing

- **WHEN** a Claude request log is recorded with a suffixed Fable model identifier
- **THEN** the pricing resolver uses the canonical `claude-fable-5` price table entry

#### Scenario: Historical Fable rows are repairable from persisted tokens

- **WHEN** historical `claude-fable-5` request-log rows have persisted token counts but `cost_usd` is null
- **THEN** operators can backfill `cost_usd` using the same Fable pricing inputs used for new rows
