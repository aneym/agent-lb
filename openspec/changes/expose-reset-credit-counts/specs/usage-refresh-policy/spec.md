## ADDED Requirements

### Requirement: Cached reset-credit counts are maintained opportunistically

The system SHALL maintain an in-memory per-account count of available OpenAI rate-limit reset credits, derived from upstream credit listings it already performs; it MUST NOT introduce new periodic upstream credit-listing traffic solely to refresh the count. Every upstream credit listing — the expiry sweep, the exhaustion-ranking sweep, and the operator listing endpoint — MUST record the observed available count. After a successful redemption the system MUST refresh the account's count best-effort and MUST discard the stale cached value when that refresh fails. The count for an account is unknown until first observation and after a server restart until the next observation.

#### Scenario: Expiry sweep refreshes all serving accounts

- **WHEN** the hourly expiry sweep lists credits for the serving OpenAI pool
- **THEN** each listed account's cached available count reflects that listing

#### Scenario: Redemption updates the count

- **WHEN** a reset credit is redeemed for an account, manually or by the scheduler
- **THEN** the account's cached count is refreshed from upstream on a best-effort basis
- **AND** a failed refresh leaves the count unknown rather than stale

#### Scenario: Menu bar polling causes no upstream traffic

- **WHEN** a client polls the accounts list at any frequency
- **THEN** no upstream credit-listing request results from those polls
