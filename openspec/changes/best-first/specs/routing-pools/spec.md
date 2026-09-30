## ADDED Requirements

### Requirement: Best workers remain first until unavailable
The interim implement ladder SHALL order grok-low, sol-medium, composer, swe2-high, sonnet-standin. Grok low SHALL use cursor-seat, grok-latest-low, low effort, maker xai and pool cursor-models without a gate. The interim mechanical ladder SHALL order composer, grok-low, swe2-medium, sol-low. The interim explore ladder SHALL order sol-low, composer, swe2-medium, sonnet-explore-standin, with Composer and SWE-2 acting as read-only explorers. Composer and SWE-2 high implementation gates SHALL be open with the note "fallback (routing-next approved 2026-09-30)". Baseline, the ladder switch and verification ladders SHALL remain unchanged.

#### Scenario: Codex runs low but is eligible
- **GIVEN** Codex is eligible and running low and Cursor is eligible
- **WHEN** implement, mechanical and explore picks run
- **THEN** they select grok-low resolved to grok-4.7-low, composer and sol-low respectively

#### Scenario: Cursor models becomes empty
- **GIVEN** Cursor models has no eligible accounts and Codex is eligible
- **WHEN** implement, mechanical and explore picks run
- **THEN** they select sol-medium, swe2-medium and sol-low respectively

#### Scenario: Both worker pools become empty
- **GIVEN** Cursor models and Codex have no eligible accounts
- **WHEN** implement, mechanical and explore picks run
- **THEN** they select swe2-high, swe2-medium and swe2-medium respectively

#### Scenario: Cursor worker makers receive a Claude reviewer
- **GIVEN** anthropic-general is eligible and not running low
- **WHEN** verification is picked with --author-maker xai or --author-maker cursor (an alias of --author-vendor)
- **THEN** the pick selects sonnet-high on sonnet-verifier rather than a Cursor seat

### Requirement: Only configured pools are pace guarded
The ladder walker SHALL apply low-pace demotion only to pools in policy.pace.guarded_pools when that key is present. The canonical table SHALL guard anthropic-general and anthropic-fable. A missing key SHALL preserve all-pool low-pace demotion. Rich promotion, exhaustion and other availability checks SHALL remain unchanged.

#### Scenario: Claude remains paced
- **GIVEN** anthropic-general is running low and another verification rung is open
- **WHEN** verification for an OpenAI author is picked
- **THEN** Sonnet on anthropic-general is demoted by pace

#### Scenario: An older table retains its behavior
- **GIVEN** guarded_pools is absent and Codex is running low
- **WHEN** an eligible fallback exists
- **THEN** Codex is demoted by pace
