## ADDED Requirements

### Requirement: Continuous OpenAI weekly-window priming

On each background usage refresh, the scheduler SHALL detect an unstarted OpenAI quota window from fresh telemetry reporting zero usage and a reset timestamp within five minutes of one full reported window length after the sample. This SHALL support a weekly primary window and plans with a five-hour primary plus weekly secondary window. When eligible, the scheduler SHALL send one minimal OpenAI Responses primer without requiring prior exhaustion or planner working hours. It MUST retain global and per-account opt-in, safe-account checks, and durable deduplication.

#### Scenario: Unstarted window after a partial reset

- **GIVEN** an active OpenAI account opted in globally and per account with fresh usage showing an unstarted window after a partially used reset
- **WHEN** the next usage refresh runs outside working hours
- **THEN** the scheduler SHALL send one primer during that refresh even though the previous window was not exhausted
- **AND** it SHALL record a successful continuous attempt and expose its completion time as last primed

#### Scenario: Started window is never primed

- **GIVEN** a fresh sample with zero usage but a reset lead shorter than a full window by more than five minutes, or with nonzero usage
- **WHEN** the scheduler evaluates continuous priming
- **THEN** the scheduler MUST NOT send a continuous OpenAI primer

#### Scenario: Exhausted or stale telemetry blocks priming

- **GIVEN** a fresh primary or secondary sample reporting exhausted quota, or no fresh sample reporting an unstarted window
- **WHEN** the scheduler evaluates continuous priming
- **THEN** the scheduler MUST NOT send a continuous OpenAI primer
- **AND** stale secondary telemetry SHALL neither block nor trigger priming from a fresh eligible primary

#### Scenario: Opt-in and account safety gate the primer

- **GIVEN** global warm-up is disabled, the account has opted out, or the account is not active or subscription-usable
- **WHEN** usage refresh sees an unstarted OpenAI window
- **THEN** the scheduler MUST NOT send a primer

#### Scenario: Hourly buckets and spacing limit repeat sends

- **GIVEN** an unstarted window persists after a continuous primer
- **WHEN** further usage refreshes run
- **THEN** the scheduler SHALL claim at most once per account per hour bucket
- **AND** it MUST NOT send another primer within 30 minutes of a non-failed primer, even after an hour boundary
- **AND** a later reset within the same weekly period MAY be primed in a later hourly bucket

#### Scenario: Explicit failure retries without replaying pending sends

- **GIVEN** a primer attempt explicitly failed
- **WHEN** usage refresh sees the unstarted window again
- **THEN** the attempt MAY be retried after bounded exponential backoff up to the retry limit
- **AND** the scheduler MUST wait six hours after an exhausted retry bucket before making a new claim
- **AND** a pending attempt MUST NOT be retried because its upstream outcome is uncertain

#### Scenario: Request logging fails after an accepted primer

- **GIVEN** OpenAI accepted a continuous primer
- **WHEN** recording the request log fails
- **THEN** the system MUST retain the succeeded attempt and MUST NOT retry that accepted primer in the same hour bucket
- **AND** it SHALL log only the exception type, not upstream response text
