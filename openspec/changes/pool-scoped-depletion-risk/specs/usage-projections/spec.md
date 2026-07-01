## ADDED Requirements

### Requirement: Pool depletion risk reflects the pool, with worst-account attribution

Aggregate depletion risk reported by `/api/dashboard/projections` MUST be
pool-level — the mean of per-account risks — because the balancer fails over
between accounts and a single account exhausting does not exhaust the pool. The
single worst account MUST be attributed alongside (id, email, risk, level, and the
number of accounts aggregated) so operators can identify what drives an elevated
pool risk. Exhaustion projections MUST NOT be rendered for a pool that is not at
danger/critical risk.

#### Scenario: One hot account does not label a calm pool critical

- **GIVEN** five accounts where one burns fast enough to project its own exhaustion and four are near idle
- **WHEN** `/api/dashboard/projections` aggregates depletion
- **THEN** the pool `riskLevel` stays below `danger`
- **AND** `worstAccountId`/`worstAccountEmail`/`worstRiskLevel` identify the hot account
- **AND** `projectedExhaustionAt` and `secondsUntilExhaustion` are null

#### Scenario: Depletion is broken down by provider

- **GIVEN** accounts from more than one provider
- **WHEN** `/api/dashboard/projections` responds
- **THEN** `depletionPrimaryByProvider` and `depletionSecondaryByProvider` map each provider to its own pool-level depletion with its own attribution
- **AND** a scoped UI reading its provider's entry never inherits another provider's risk

#### Scenario: Elevated pool risk carries a consistent projection

- **GIVEN** enough accounts burning fast that the mean risk classifies danger or critical
- **WHEN** the aggregate is built
- **THEN** the exhaustion projection fields carry the worst account's ETA for operator urgency
