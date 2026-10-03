## ADDED Requirements

### Requirement: Request logs carry room attribution and upstream utilization
Request logs MUST expose nullable room, unified_5h_utilization, and unified_7d_utilization fields. The room MUST be the stripped, lowercased x-agent-lb-room value only when it matches `[a-z0-9][a-z0-9@._:/-]{0,127}\Z`; missing or invalid values MUST remain null without rejecting requests. Anthropic utilization MUST come from the logged attempt's upstream response headers, parsed as finite nonnegative floats without scaling. Other providers MUST record null utilization. Logging MUST NOT alter request payloads, upstream forwarded headers, downstream response bytes or headers, account selection, or existing tripwire logic.

#### Scenario: Anthropic response includes quota evidence
- **WHEN** a streaming or non-streaming Anthropic response carries unified five-hour and seven-day utilization headers
- **THEN** its success or error log row stores the finite nonnegative raw values and normalized room

#### Scenario: Missing or malformed attribution
- **WHEN** room or utilization headers are missing or invalid
- **THEN** the corresponding fields are null and the request behavior is unchanged

#### Scenario: Other provider request
- **WHEN** a Codex request carries a valid room header
- **THEN** its log row stores the room and null utilization values

#### Scenario: Existing database is upgraded
- **WHEN** the migration upgrades a database containing request logs
- **THEN** existing rows retain their data with null new fields and repeated upgrades are safe
- **AND** downgrading removes only the new columns
