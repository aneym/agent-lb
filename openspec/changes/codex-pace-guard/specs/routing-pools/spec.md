## ADDED Requirements

### Requirement: Codex is pace guarded while it runs low
The canonical table SHALL list openai-codex in policy.pace.guarded_pools alongside anthropic-general and anthropic-fable. While openai-codex is running low, Sol rungs SHALL rank after every open rung and SHALL remain routable as soft rungs. When openai-codex is no longer running low, the Sol rungs SHALL return to their ladder positions without a table edit.

#### Scenario: Codex runs low and Cursor is eligible
- **GIVEN** Codex is eligible and running low and Cursor models is eligible
- **WHEN** implement, mechanical and explore picks run
- **THEN** each selects composer, and its Sol rung is listed as running low

#### Scenario: Cursor models becomes empty while Codex runs low
- **GIVEN** Cursor models has no eligible accounts and Codex is eligible and running low
- **WHEN** implement, mechanical and explore picks run
- **THEN** they select swe2-high, swe2-medium and swe2-medium respectively

#### Scenario: Claude-authored work keeps its Sol reviewer
- **GIVEN** Codex and anthropic-general are both running low
- **WHEN** verification is picked for an anthropic author
- **THEN** the pick selects sol-xhigh on codex-verifier

#### Scenario: Cursor and Devin authors get Sonnet first
- **GIVEN** Codex and anthropic-general are both running low and the orchestrator reserve on anthropic-general is inactive
- **WHEN** verification is picked for an xai, cursor or cognition author
- **THEN** the pick selects sonnet-high on sonnet-verifier; while the reserve is active, the existing sonnet-cursor-high adapter on cursor-seat may lead instead

### Requirement: Grok rungs hold while Grok is out of usage
The interim grok-medium and grok-low rungs SHALL carry a closed gate whose note names the evidence and the reopen condition (the Cursor cycle reset or a passing probe). A gated Grok rung SHALL be skipped with a reason that starts with "gated:".

#### Scenario: Implement skips gated Grok
- **GIVEN** the canonical table and an eligible Cursor models pool
- **WHEN** an implement pick runs
- **THEN** it selects composer and lists grok-medium as gated
