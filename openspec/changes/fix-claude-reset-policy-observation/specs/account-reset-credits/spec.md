## ADDED Requirements

### Requirement: Policy inventory follows the threshold gate
The policy SHALL fetch reset-credit inventory only when every live Claude account has known remaining quota below 5%. An inventory error SHALL produce `hold inventory_unavailable:<account>` with exit 0 and prevent redemption.

#### Scenario: Account above threshold
- **WHEN** a live account has remaining quota at or above 5%
- **THEN** the policy reports `hold above_threshold:<account>=<percent>` without requesting inventory

#### Scenario: Inventory returns HTTP 409
- **WHEN** an inventory request fails with HTTP 409 due to upstream HTTP 429
- **THEN** the policy reports `hold inventory_unavailable:<account>` with exit 0

### Requirement: Policy burn uses persisted window observations
Burn SHALL use the newest sample at least five minutes old with the same account, window and reset time. Missing usable history SHALL produce `hold no_burn_sample`. The policy SHALL use one status observation per run.

#### Scenario: Stored consumption establishes burn
- **WHEN** a matching window sample ten minutes old has 9% remaining and the current window has 4%
- **THEN** the policy uses a burn rate of 30 percentage points per hour in its runway decision

#### Scenario: No matching sample
- **WHEN** a live window has no usable prior sample
- **THEN** the policy holds with `no_burn_sample` and persists the current observation

### Requirement: Policy sample storage is bounded
Each live policy run SHALL persist account ID, window, window reset time, remaining percentage and observation time. The default path SHALL be `~/.agent-lb/claude-reset-policy-samples.json`, overridable with `--samples PATH`. Samples older than 48 hours SHALL be discarded.

#### Scenario: History exceeds retention
- **WHEN** a run loads samples older than 48 hours
- **THEN** it excludes them from burn calculations and the saved history
