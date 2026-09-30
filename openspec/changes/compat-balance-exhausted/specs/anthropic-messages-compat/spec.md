## ADDED Requirements

### Requirement: Compatible provider balance failures remain parked
The proxy SHALL pause GLM accounts on upstream code 1113 and Kimi accounts on suspended or insufficient-balance errors, recording `balance_exhausted: <upstream code>` without a rolling cooldown. Subsequent requests SHALL report no balance without contacting that account upstream. The existing reactivate operation SHALL clear the pause and reason. Account status and pool presentation SHALL distinguish no balance from temporary quota exhaustion.

#### Scenario: Balance restored by an operator
- **WHEN** GLM returns code 1113 for a Messages request
- **THEN** the account is paused with reason `balance_exhausted: 1113` and no reset deadline
- **AND** the next request reports no balance without contacting upstream
- **WHEN** the account is reactivated through the existing operation
- **THEN** routing can contact that account again
