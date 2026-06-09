## ADDED Requirements

### Requirement: Anthropic conversations use durable account stickiness
Anthropic Messages proxy requests SHALL derive a privacy-preserving sticky key from Claude Code conversation/session headers when available, falling back to stable first-turn request material when no explicit session header exists. The sticky key SHALL be hashed before persistence and SHALL use durable `codex_session` stickiness so repeated turns in one Claude conversation prefer the same Anthropic account while that account remains eligible for the requested quota key.

#### Scenario: Same Claude session keeps the same Anthropic account
- **GIVEN** two active Anthropic OAuth accounts
- **AND** Claude Code sends repeated Messages requests with the same session identifier
- **WHEN** the pinned account remains eligible for the request's quota key
- **THEN** codex-lb sends both requests through the same Anthropic account
- **AND** the persisted sticky-session key does not contain the raw session identifier

#### Scenario: Quota cooldown can rebind the sticky account
- **GIVEN** a Claude session is pinned to one Anthropic account
- **AND** that account is cooling down for the requested quota key
- **WHEN** another Anthropic account remains eligible for that quota key
- **THEN** codex-lb selects the eligible account and updates the durable sticky mapping for subsequent turns
