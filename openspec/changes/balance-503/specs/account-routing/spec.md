## ADDED Requirements

### Requirement: Account-rule exhaustion preserves pool eligibility
The proxy SHALL distinguish handled account rules from transient upstream failures. After balance rules pause all attempted accounts, attempt exhaustion SHALL return the provider no-available-accounts code and eligibility reset time. Genuine upstream transient 5xx responses SHALL continue to fail over and preserve their upstream status on exhaustion without a pool-reset wait.

#### Scenario: Balance exhausts the attempt budget
- **WHEN** every attempted account reports exhausted balance and the attempt budget is reached
- **THEN** the proxy returns the provider no-available-accounts code with the pool eligibility reset time

#### Scenario: Transient outage
- **WHEN** an upstream returns transient 503 and a later account succeeds
- **THEN** the request succeeds using the later account
