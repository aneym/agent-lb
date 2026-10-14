## ADDED Requirements

### Requirement: Preserve subscription quota evidence
The proxy SHALL retain every `anthropic-ratelimit-unified-*` response header and its observation timestamp with the account and existing caller tags for each logged response attempt.

#### Scenario: Streaming or failed response
- **WHEN** an Anthropic response has unified headers
- **THEN** its receipt retains the headers, parsed 5h/7d utilization, account, room, seat and caller identity even when the response fails

### Requirement: Attribute measured utilization increases
The quota command SHALL output JSON or a short table of utilization percentage points per caller per account and the top five consumers. It SHALL attribute increases between consecutive readings within the same reset window using uncached input plus output tokens. If any interval request lacks tokens, or total weight is zero, it SHALL use equal request shares and state this method.

#### Scenario: Cache reads and reset boundaries
- **WHEN** two readings differ and callers have requests between them
- **THEN** cache-read tokens do not weight attribution, reset transitions and utilization decreases are excluded, and missing evidence is reported rather than treated as zero measured usage

#### Scenario: Selected time window
- **WHEN** a report starts between readings
- **THEN** the report uses the preceding reading and all interval requests to apportion the delta, emitting only shares belonging to requests in the selected window
