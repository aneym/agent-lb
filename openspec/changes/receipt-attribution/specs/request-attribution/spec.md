## ADDED Requirements

### Requirement: Receipts carry lane, job and decision ids
The proxy SHALL store `x-agent-lb-lane`, `x-agent-lb-job` and `x-agent-lb-dispatch` on the request log row as `lane`, `job_id` and `dispatch_id`. A value that fails validation SHALL be stored as null. A request without lane and job headers SHALL inherit the values last seen on the same client session id. These headers SHALL NOT be forwarded upstream.

#### Scenario: Seat launched with a dispatch document
- **GIVEN** `AGENT_LB_DISPATCH` carries `job_id` and `decision_id`, `AGENT_LB_SEAT` is set, and `LANE_DIR` ends in `eff-r8`
- **WHEN** the seat sends a request, then a second untagged request on the same session
- **THEN** both rows carry lane `eff-r8` and the job id, and the first carries the seat and the decision id

### Requirement: Token audit reports lane coverage
`audit tokens` SHALL report `attribution.coverage` as the share of priced USD whose receipt has a lane source, and `attribution.unattributed_usd` and `unattributed_pct` for the rest. With no priced spend, `coverage` and `unattributed_pct` SHALL be null.

#### Scenario: No priced spend
- **GIVEN** receipts that all price at $0
- **WHEN** the audit runs
- **THEN** coverage and unattributed_pct are null, not 0

### Requirement: Synthetic decisions are excluded from the join rate
`route dispatch` SHALL write `synthetic: true` on decisions made under tests, `ROUTE_SYNTHETIC=1`, or a test or probe job or caller. `audit routing` SHALL report the share of non-synthetic decisions in the window that have a matching execution.

#### Scenario: Probe decision
- **GIVEN** one joined real decision, one unjoined real decision, and two synthetic decisions
- **WHEN** the join is computed
- **THEN** decisions is 2, synthetic_excluded is 2, and join_rate is 0.5
