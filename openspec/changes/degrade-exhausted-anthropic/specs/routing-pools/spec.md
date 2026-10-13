## ADDED Requirements

### Requirement: Live empty Anthropic pools degrade seat aliases
The resolver SHALL substitute sol-latest for opus-latest and sonnet-latest only when the live Anthropic pool reports no eligible accounts. It SHALL return degraded_from metadata in JSON and record a routing ledger event. It SHALL NOT degrade Fable aliases or switch on low headroom labels or accounts-fallback estimates.

#### Scenario: Live pool empties and recovers
- **WHEN** the general Anthropic pool has no eligible accounts
- **THEN** Opus and Sonnet alias resolution selects Sol and names the original alias
- **WHEN** an account becomes eligible again
- **THEN** the requested alias resolves to Anthropic again without a persistent override
