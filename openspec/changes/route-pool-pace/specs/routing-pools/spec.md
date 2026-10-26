## ADDED Requirements

### Requirement: One shadow pace computation in agent-lb
agent-lb SHALL compute pace for the anthropic and openai pools in one module. For each pool it SHALL report `tier`, `candidate_tier`, `b_star`, `floor`, `rho`, `rho_fast`, `rho_slow`, `b_fast`, `b_slow`, `short_window_capacity`, `plan_factors`, `eligible_accounts`, `stale` and `missing`, and the document SHALL carry `s_star`, `shadow: true` and `enforced: false`.

b* SHALL be the largest constant burn for which a forward simulation, draining the earliest-reset account first and resetting each account to 100 times its plan factor at its reset, never drives any account below its reserve (10 points, tapering to 0 over the last 12 hours) before a refill. rho SHALL be b* divided by the larger of the 2-hour and 24-hour burn.

Tiers SHALL be rich at rho 1.3 or more, ahead at 1.1, on_pace at 0.9, behind at 0.7, otherwise critical. A pool SHALL step down at once and step up only after two ticks with the same better candidate. Stale or incomplete input SHALL hold the tier at behind or lower.

The client SHALL read this pace (`route pace`, `route pools`, ladder walks) and SHALL NOT compute its own. No admission decision SHALL depend on the tier until enforcement is enabled by a later change.

#### Scenario: Shadow tier on the pools document
- **WHEN** `/api/pools` is read
- **THEN** the anthropic-general and openai-codex pools carry the same `pace` as `/api/pools/pace`

#### Scenario: Stale quota caps the tier
- **WHEN** any account in a pool has no quota reading in the last 30 minutes
- **THEN** that pool's tier is behind or lower and `stale` is true

#### Scenario: Opus rung under a min_tier
- **WHEN** an implement ladder reaches the Opus rung while the Anthropic tier is below its `min_tier`
- **THEN** the rung is admitted and the dispatch notes the shadow tier
