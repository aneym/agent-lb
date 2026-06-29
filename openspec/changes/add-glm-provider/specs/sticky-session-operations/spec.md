## ADDED Requirements

### Requirement: GLM Messages stickiness cannot collide with Claude stickiness
GLM Messages proxy requests SHALL derive sticky-session keys with a GLM-specific prefix. Claude/Anthropic Messages requests SHALL continue using the Claude/Anthropic sticky prefix. Raw client session identifiers SHALL remain hashed before persistence.

#### Scenario: Same GLM session keeps the same GLM account
- **GIVEN** two active GLM accounts
- **AND** repeated GLM Messages requests include the same session identifier
- **WHEN** the pinned GLM account remains eligible for the requested GLM quota key
- **THEN** agent-lb sends both requests through the same GLM account
- **AND** the persisted sticky-session key does not contain the raw session identifier

#### Scenario: GLM and Claude session IDs remain separate
- **GIVEN** one GLM request and one Claude request use the same downstream session identifier
- **WHEN** both requests are routed
- **THEN** the persisted sticky-session keys differ by provider prefix
- **AND** neither request can reuse the other provider's pinned account
