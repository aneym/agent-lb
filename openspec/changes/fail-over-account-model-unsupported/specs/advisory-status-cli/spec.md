## ADDED Requirements

### Requirement: Absent usage windows do not block usability
The status CLI SHALL treat a usage window reported as `null` as absent. An active
account with at least one observed, non-exhausted window SHALL be reported usable.

#### Scenario: Weekly-only plan
- **GIVEN** an active ChatGPT Pro account whose accounts API usage is `{"primaryRemainingPercent": null, "secondaryRemainingPercent": 59}`
- **WHEN** `agent-lb status` runs
- **THEN** the account is reported usable
