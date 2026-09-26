## ADDED Requirements

### Requirement: Implementation defaults to Sol at medium effort
The implementation and mechanical heads SHALL be gpt-implementer on sol-latest at medium effort. The fallback SHALL reflect measured admission, not an inferred quota rule.

#### Scenario: Healthy pool
- **WHEN** `route pick implement` runs with healthy pools
- **THEN** it selects gpt-implementer on sol-latest

#### Scenario: Low Codex pool
- **WHEN** the Codex pool is low and gpt-implementer is available
- **THEN** it still selects gpt-implementer

#### Scenario: Implementer recorded down
- **WHEN** gpt-implementer is recorded down, Anthropic is on pace and above low, and Codex is above critical
- **THEN** it selects opus-seat

#### Scenario: Codex exhausted
- **WHEN** the Codex pool is exhausted
- **THEN** nothing is routable and the command exits 2

#### Scenario: Mechanical default
- **WHEN** the mechanical chain is read
- **THEN** its head is gpt-implementer

### Requirement: Off-default implementers stay off every chain
Off-default seats SHALL carry dated evidence and an evaluation return condition.

#### Scenario: An off-default seat appears in a chain
- **WHEN** a chain names an off_default seat
- **THEN** `verify-routing --source-only` exits 1 with `factory:off-default`

### Requirement: Every implementation unit is audited by the other vendor
Every unit and fix round SHALL have a fresh other-vendor verifier and a release-facts check. Anthropic authors use codex-verifier on sol-latest at xhigh; openai, cursor, devin, glm and kimi authors use verifier on opus-latest at high.

#### Scenario: Same-vendor auditor
- **WHEN** an audit mapping selects the author's vendor
- **THEN** source-only verification exits 1 with `factory:audit-cross-vendor`

### Requirement: Money-path review is a three-lens any-fail panel
Money-path review SHALL use three lenses at xhigh, any failure blocks, and fix rounds SHALL be capped at two. A FAIL SHALL require re-verification before acceptance.

#### Scenario: Majority vote substituted
- **WHEN** the money-path rule is majority rather than any-fail
- **THEN** source-only verification exits 1 with `factory:review-policy`

### Requirement: Effort is fixed per stage
Chain effort SHALL be in the class band; forwarders relay at low and their worker effort is the chain entry's effort; a ccgpt definition's model suffix SHALL equal its entry's effort.

#### Scenario: Effort does not match the stage
- **WHEN** a chain effort, forwarder relay effort or ccgpt model suffix violates its band
- **THEN** source-only verification exits 1 with `factory:stage-effort`

### Requirement: Complex plans get a Sol second opinion
The plan's second opinion SHALL be sol-consult on sol-latest at high effort before splitting complex or risky plans.

#### Scenario: Missing second opinion
- **WHEN** the plan second_opinion is removed
- **THEN** source-only verification exits 1 with `factory:second-opinion`

### Requirement: The canon is verifiable without an installed home
Source-only verification SHALL not access installed policy, an LB or the real home.

#### Scenario: Empty home and unreachable LB
- **WHEN** `verify-routing --source-only` runs with an empty HOME and unreachable LB
- **THEN** it exits 0 on a conforming canon, leaves HOME empty and names each violated rule on failure

### Requirement: Canon rules are dated and short
Every ROUTING.md level-two heading SHALL contain a date and the document SHALL be at most 150 lines.

#### Scenario: Undated or oversized canon
- **WHEN** a heading is undated or the canon exceeds 150 lines
- **THEN** source-only verification exits 1 with `factory:routing-doc`

### Requirement: The canon carries no host or personal literals
Public policy and managed definitions SHALL contain no personal names, emails, hostnames or local absolute checkout paths.

#### Scenario: Personal path in a managed definition
- **WHEN** a personal home path is appended to a managed definition
- **THEN** source-only verification exits 1 with `factory:public-text`
