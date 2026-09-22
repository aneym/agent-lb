## ADDED Requirements

### Requirement: Read-only status awareness
The CLI SHALL offer human and JSON account status, provider and model filters,
quota/reset/freshness details, and current routing-policy observations without
inference, account mutation, or automatic dispatch denial.

#### Scenario: Exhausted capacity is an observation
- **WHEN** all observed accounts are exhausted and status is retrieved successfully
- **THEN** the command reports the limits and exits zero without preventing work

#### Scenario: Service or telemetry unavailable
- **WHEN** required status endpoints cannot be read or return malformed data
- **THEN** the CLI returns a structured error and nonzero status, not zero capacity

#### Scenario: Existing server invocation
- **WHEN** agent-lb is invoked without a status subcommand
- **THEN** existing server startup behavior is preserved

### Requirement: Capacity guards are advisory
Local reserve estimates or missing quota snapshots SHALL NOT deny agent launches.
Actual provider limits, authentication, and explicit spending restrictions remain.

#### Scenario: Remaining scoped Fable capacity
- **GIVEN** an otherwise usable account has 90 percent scoped Fable usage
- **WHEN** the default routing policy considers it
- **THEN** it is not excluded merely to preserve an artificial ten-percent reserve
