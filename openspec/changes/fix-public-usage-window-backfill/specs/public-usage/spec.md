## ADDED Requirements

### Requirement: Public usage windows expose exactly the requested number of daily buckets

The public usage endpoint SHALL return exactly `period.days` contiguous UTC date
buckets in both `daily` and `trends`. The range SHALL include the current UTC
date as the final bucket and SHALL start at `current_utc_date - (period.days - 1)`.

#### Scenario: Seven-day public usage window has seven buckets

- **WHEN** a client requests `/api/usage/public?days=7`
- **THEN** the response `period.days` is `7`
- **AND** `daily` contains exactly 7 rows
- **AND** `trends` contains exactly 7 rows
- **AND** the final row date is the current UTC date

### Requirement: Published usage snapshots are complete backfill artifacts

The public usage publisher SHALL write the configured window snapshots
(`usage-7.json`, `usage-30.json`, `usage-90.json`, `usage-365.json`, and
`usage.json`) as complete aggregate backfill artifacts. Each snapshot SHALL
conform to the public usage response contract and SHALL contain no raw request,
account, credential, or identity data.

#### Scenario: Website consumes a published snapshot

- **WHEN** the website fetches a published `usage-*.json` snapshot from the public repository
- **THEN** the snapshot contains a live public usage payload
- **AND** its `daily` and `trends` arrays each contain exactly `period.days` rows
- **AND** the payload contains only aggregate usage fields
