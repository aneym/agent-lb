# proxy-runtime-observability Delta

## ADDED Requirements

### Requirement: Startup summary attributes time outside named phases

The startup summary log MUST include `untracked_seconds` as a top-level
key-value field computed as the non-negative difference between total startup
seconds and the sum of named phase durations. The field MUST NOT be added to the
phases JSON object.

#### Scenario: Cold imports precede named startup phases

- **WHEN** process-start-to-complete time exceeds the sum of named phases
- **THEN** `untracked_seconds` reports the difference
- **AND** the phases JSON retains only named phases

#### Scenario: Rounded phase durations exceed the measured total

- **WHEN** the summed phase durations meet or exceed total startup duration
- **THEN** `untracked_seconds` is zero rather than negative
