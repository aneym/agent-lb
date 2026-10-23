## ADDED Requirements

### Requirement: Current account quota display
The menubar SHALL refresh account quota telemetry on each 30-second closed-panel polling tick and each five-second open-panel tick. It SHALL retain authoritative API remaining percentages and rate-limit reset dates without inventing local quota resets.

#### Scenario: Backend records live exhaustion
- WHEN the backend records exhausted five-hour quota and its reset
- THEN the next successful account poll SHALL display the API values

#### Scenario: Runtime rate-limit reset missing
- WHEN an account is rate-limited and the API omits its runtime reset or reports an earlier runtime reset while an exhausted quota window has a reset
- THEN the menubar SHALL display the latest exhausted-window reset without using an allowed window reset

### Requirement: Verifiable installed source
Built app bundles SHALL include the source revision. Studio and Book deployment SHALL preserve local preferences and API configuration.

#### Scenario: Synchronized app rollout
- WHEN the same signed app bundle is installed on Studio and Book
- THEN both installed bundles SHALL report the same source revision and binary digest
