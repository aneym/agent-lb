## ADDED Requirements

### Requirement: Consistent bounded refusal guards
When an account has a real refusal marker, runtime cooldown normalization MUST use the same bounded retry policy as its persisted reset. A legacy reset beyond the supported retry bound MUST NOT independently extend runtime exclusion. Valid bounded retry intervals, including fractional runtime timestamps paired with integral persisted markers, and marker-free transient cooldowns MUST remain enforced.

#### Scenario: Legacy deadline expires at the bounded retry
- **GIVEN** a refusal marker and legacy persisted and runtime deadlines beyond one hour
- **WHEN** the existing bounded retry has expired
- **THEN** the selector admits the account despite stale exhausted telemetry

#### Scenario: Genuine bounded retry remains active
- **GIVEN** a refusal with a valid future retry interval within one hour
- **WHEN** selection occurs before that deadline
- **THEN** the account remains excluded

### Requirement: Selection diagnostics retain runtime exclusion evidence
An Anthropic selection failure MUST retain the selector reason and bounded retry deadline and select the earliest future retry across live selector, requested-quota cooldown, and response-written extra-usage tripwire guards on the pool. Snapshot window deadlines MUST remain fallback only when no live retry is available. Runtime cooldown exclusions MUST be counted separately from stored account statuses; a stored active status MUST NOT hide the reason for exclusion.

#### Scenario: Runtime-only cooldown excludes active accounts
- **GIVEN** active stored accounts excluded by live runtime cooldowns
- **WHEN** the session-route endpoint returns a selection failure
- **THEN** its error includes the selector reason, runtime cooldown count, and selector retry deadline
