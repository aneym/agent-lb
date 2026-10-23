## ADDED Requirements

### Requirement: Error-rule cooldown exclusions are independent
The selector MUST exclude an account under an error-rule cooldown until its configured deadline, without applying a rate-limit penalty. Ordinary cooldowns MUST retain last-resort fallback when enabled.

#### Scenario: Ordinary cooldown in a single-account pool
- **GIVEN** the only account has a future ordinary cooldown
- **AND** quota status is bypassed and fallback is enabled
- **WHEN** a request selects an account
- **THEN** that account is selected

#### Scenario: Error-rule cooldown expires
- **GIVEN** the only account receives an error-rule cooldown
- **WHEN** another request arrives before its deadline
- **THEN** the account is excluded
- **WHEN** the configured duration expires
- **THEN** it becomes selectable without an additional penalty
