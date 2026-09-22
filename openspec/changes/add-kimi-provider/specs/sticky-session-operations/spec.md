## ADDED Requirements

### Requirement: Kimi Messages stickiness cannot collide with other providers
Kimi Messages proxy requests SHALL derive sticky-session keys with a Kimi-specific prefix. Claude/Anthropic and GLM Messages requests SHALL continue using their own sticky prefixes. Raw client session identifiers SHALL remain hashed before persistence.

#### Scenario: Same Kimi session keeps the same Kimi account
- **GIVEN** two active Kimi accounts
- **AND** repeated Kimi Messages requests include the same session identifier
- **WHEN** the pinned Kimi account remains eligible for the requested Kimi quota key
- **THEN** agent-lb sends both requests through the same Kimi account
- **AND** the persisted sticky-session key does not contain the raw session identifier

#### Scenario: Kimi and Claude session IDs remain separate
- **GIVEN** one Kimi request and one Claude request use the same downstream session identifier
- **WHEN** both requests are routed
- **THEN** the persisted sticky-session keys differ by provider prefix
