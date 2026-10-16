## ADDED Requirements

### Requirement: Periodic bounded uncertain exchange recovery
The service SHALL revisit locally owned uncertain exchanges through the usage
scheduler and normal freshness checks. Recovery MUST use the database-clock
processing window and atomic ownership/token fences. It MUST NOT reopen
permanent auth failures or replay an unsaved success or ambiguous replay.
Usage freshness, auth cooldowns and near-reset priority MUST NOT suppress this
recovery pass.

#### Scenario: Usable access token does not strand an eligible exchange
- **GIVEN** a locally owned uncertain account with a usable access token and fresh usage
- **AND** its matching first exchange has passed the database-clock processing window
- **WHEN** the periodic usage scheduler runs
- **THEN** the service attempts the existing fenced replay without an operator reset
- **AND** a persisted success restores active status

#### Scenario: Recovery remains bounded
- **GIVEN** an uncertain exchange is still processing, already replayed, unsaved, missing, or mismatched
- **WHEN** normal freshness or the periodic scheduler revisits the account
- **THEN** no automatic refresh replay is sent

#### Scenario: Permanent failure and remote ownership stay closed
- **GIVEN** an account requires reauthentication, is deactivated, or belongs to another instance
- **WHEN** automatic recovery runs
- **THEN** the service does not send a token refresh for that account
