## ADDED Requirements

### Requirement: Opus scoped observations
The local status model assessment MUST observe Opus-specific thinking and non-thinking keys rather than unrelated top-model headroom. Exhausted Opus rows MUST produce an unknown advisory assessment, not a whole-window blocked verdict or a usable claim, because snapshots do not expose live bounded refusal recovery.

#### Scenario: Thinking Opus marker contradicts legacy headroom
- **GIVEN** healthy legacy top-model telemetry and an exhausted Opus thinking row
- **WHEN** status assesses Opus with thinking enabled
- **THEN** the model assessment is unknown and explains the observed scoped exhaustion and bounded retry
