## ADDED Requirements

### Requirement: Service recovery starts the approved internal runtime
The macOS Agent LB launcher MUST start the existing internal runtime without
reading or synchronizing the external development checkout. It MUST retain the
runtime working directory, inherited environment, argument boundaries, standard
output/error streams and executable replacement behavior.

#### Scenario: External source unavailable
- **WHEN** the external source cannot be accessed
- **THEN** service startup executes the approved internal runtime
- **AND** startup does not invoke the source sync helper

#### Scenario: Explicit source deployment
- **WHEN** an operator deploys source changes
- **THEN** the configured sync helper runs in the authorized interactive context
- **AND** new sync log entries and all reviewed runtime hashes are verified
- **AND** restart occurs only in the coordinated service window

#### Scenario: Deployment sync failure
- **WHEN** the helper fails or expected runtime hashes do not match
- **THEN** deployment stops without treating helper exit zero as success
- **AND** the operator does not restart or mark the candidate deployed
- **AND** the operator verifies possible partial copies against the checkpoint
- **AND** recovery follows the reviewed rollback or deployment path
