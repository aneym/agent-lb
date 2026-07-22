## ADDED Requirements

### Requirement: Extra-usage tripwire follows current primary exhaustion
Agent-lb MUST apply a persisted Anthropic extra-usage tripwire as a standard-routing exclusion until a newer primary-window usage snapshot reports subscription headroom. When the account's latest primary usage has subscription headroom and was recorded after the tripwire, agent-lb MUST admit it through normal routing despite the still-active historical tripwire. This reconciliation MUST NOT clear or bypass requested-quota cooldowns, secondary exhaustion, model eligibility, authentication, subscription, pause, circuit-breaker, or other safety gates.

#### Scenario: Recovered subscription headroom supersedes historical tripwire
- **GIVEN** an Anthropic account has an active persisted extra-usage tripwire
- **AND** a newer primary usage snapshot reports less than 100 percent used
- **WHEN** a standard Anthropic request is routed
- **THEN** the tripwire does not exclude the account
- **AND** the request uses subscription-covered routing

#### Scenario: Exhausted primary window preserves tripwire
- **GIVEN** an Anthropic account has an active persisted extra-usage tripwire
- **AND** its latest primary usage remains exhausted with a future reset
- **WHEN** a standard Anthropic request is routed
- **THEN** the tripwire remains an authoritative standard-routing exclusion
- **AND** paid fallback is available only under the existing explicit extra-usage policy

#### Scenario: Older headroom snapshot does not erase a fresh tripwire
- **GIVEN** an Anthropic response writes a new active extra-usage tripwire
- **AND** the latest primary headroom snapshot predates that response
- **WHEN** the next standard Anthropic request is routed
- **THEN** the tripwire remains an authoritative exclusion until a newer usage snapshot proves recovery
