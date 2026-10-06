## ADDED Requirements

### Requirement: seat runs a Python route through its interpreter
When `ROUTE_BIN` (default `~/.agent-lb/bin/route`) is a script whose first line names python, `seat` SHALL invoke it as `<seat's own interpreter> <route> <args>` for `route resolve`, `route reserve`, `route heartbeat` and `route release`, with the same arguments in the same order as a direct exec. Any other `ROUTE_BIN` SHALL run by its own path.

#### Scenario: A route that cannot be exec'd still reserves, runs and releases
- **GIVEN** `ROUTE_BIN` names a Python route file without the execute bit
- **WHEN** `seat run --vendor cursor --class mechanical` starts
- **THEN** it reserves through that route, runs the vendor CLI, writes a dispatch row naming the reservation and releases the hold
