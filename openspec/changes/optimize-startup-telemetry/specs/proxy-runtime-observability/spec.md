## ADDED Requirements

### Requirement: Service startup phases are observable

The service MUST emit structured startup-complete and readiness timing after each boundary and MUST expose low-cardinality Prometheus timing metrics when Prometheus is available. The summary and metrics MUST distinguish stable startup phases, startup-complete duration, and readiness duration, MUST use monotonic durations, and MUST NOT include database URLs, filesystem contents, account identifiers, request identifiers, prompts, credentials, or unbounded error text as metric labels.

#### Scenario: Successful service startup emits a phase summary

- **WHEN** the service reaches startup completion
- **THEN** it emits one `agent_lb_startup_summary` event with a boot identifier, total duration, outcome, and stable phase-duration map
- **AND** it emits `agent_lb_startup_ready` after readiness completes
- **AND** Prometheus observes phase, startup-complete, and readiness duration when metrics are available

#### Scenario: Startup phase fails

- **WHEN** a measured startup phase raises before completion
- **THEN** the phase timing records a failed outcome before the exception propagates
- **AND** the failure metric uses only a bounded outcome and stable phase label

#### Scenario: Prometheus dependency is unavailable

- **WHEN** the runtime does not have Prometheus support
- **THEN** structured startup timing still works
- **AND** startup does not fail because metrics cannot be recorded
