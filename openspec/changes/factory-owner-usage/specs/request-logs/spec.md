## ADDED Requirements

### Requirement: Factory usage attribution
The service SHALL persist factory project, owner, host and job on request log rows. Owner and host SHALL come from the serving instance configuration. Caller headers SHALL supply only project and job, with bounded identifier validation. Requests without valid factory context SHALL retain null factory fields.

#### Scenario: A caller claims another pool owner
- **WHEN** a factory request sends project and job headers alongside an owner claim
- **THEN** the stored factory owner is the serving instance owner and the claimed owner has no effect

#### Scenario: Ordinary traffic
- **WHEN** a request has no valid factory project and job headers
- **THEN** the factory fields are null
