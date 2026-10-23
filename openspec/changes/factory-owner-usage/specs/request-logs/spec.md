## ADDED Requirements

### Requirement: Factory usage attribution
The service SHALL persist factory project, owner, host and job on request log rows. Owner and host SHALL come from the serving instance configuration. Caller headers SHALL supply only project and job, with bounded identifier validation. Requests without valid factory context SHALL retain null factory fields.

#### Scenario: A caller claims another pool owner
- **WHEN** a factory request sends project and job headers alongside an owner claim
- **THEN** the stored factory owner is the serving instance owner and the claimed owner has no effect

#### Scenario: Ordinary traffic
- **WHEN** a request has no valid factory project and job headers
- **THEN** the factory fields are null


### Requirement: Read factory usage
A dedicated owner-configured token SHALL authorize a read-only factory usage endpoint. It SHALL return factory attribution, token totals, a stable row identifier and request timestamp for the requested project, without account credentials or request content. Project and job headers remain owner-attested until workload identity verification is installed.

#### Scenario: Missing usage token
- **WHEN** a caller reads factory usage without the configured token
- **THEN** the endpoint returns 403

#### Scenario: Two owners
- **WHEN** attributed requests from both owner instances are queried
- **THEN** the result retains each serving owner and host

### Requirement: Isolated usage transport and pagination
Factory project and job headers SHALL be stripped before upstream provider calls. Project and job identifiers SHALL each accept up to 128 characters. Both identifiers SHALL be required for attribution; a partial pair SHALL remain unattributed and SHALL NOT inherit a previous request's attribution. Each bridge request SHALL retain the attribution captured at its creation through success, failure and persistence.

#### Scenario: Reused bridge session
- **WHEN** requests for jobs J1 and J2 reuse one bridge session
- **THEN** their persisted rows retain J1 and J2 respectively, including when one request has no factory tags

#### Scenario: Multiple project pages with live inserts
- **WHEN** a client reads project usage with a page limit and follows nextBeforeId as beforeId
- **THEN** project filtering occurs before the SQL limit, hasMore reflects an additional matching row, and newer inserts do not repeat or displace rows in subsequent pages

#### Scenario: Invalid non-ASCII bearer
- **WHEN** an invalid non-ASCII Authorization value is sent
- **THEN** the endpoint returns 403

#### Scenario: Active custom Codex provider
- **WHEN** Codex selects a custom provider through config, profile or command-line override
- **THEN** factory header overrides target that provider and preserve its existing headers
