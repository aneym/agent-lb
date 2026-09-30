## ADDED Requirements

### Requirement: Pool pacing projects live quota through reset
`route pools --json` SHALL add a `pace` object to each pool without otherwise changing the document. The object SHALL expose `state`, `remaining`, `hours_left`, `burn_per_hour`, `empties_in_h`, `leftover` and `basis`. Text output SHALL show a pace column with `running low`, `on pace`, `running rich`, `empty` or `unknown`.

For month pools, remaining SHALL use `monthlyRemainingPercent` then `aggregateRemainingPercent`, and reset SHALL use `cycleResetAt` then `resetAt`. Other pools SHALL use `weeklyRemainingPercent` then `aggregateRemainingPercent`, and `weeklyResetAt` then `resetAt`.

Daily burn SHALL prefer numeric `burn24hPercent` divided by 24. Otherwise it SHALL use the oldest sample for this pool between 28 and 20 hours ago, dividing the decrease in remaining by elapsed hours and flooring it at zero. Any subsequent increase above the preceding sample by more than five percentage points SHALL invalidate history-derived burn as a reset.

When burn B, remaining R and hours to reset H are known, basis SHALL be `burn_24h`, time to empty SHALL be R/B (null for zero burn), and leftover SHALL be R-B*H. The state SHALL be `low` when time to empty is less than H, otherwise `rich` when leftover is at least `policy.pace.rich_leftover` (default 15), otherwise `on_pace`.

Without burn, numeric `weeklyPacePercent` (or `monthlyPacePercent` for month pools) SHALL produce basis `pace`: `low` below `behind_lt`, `rich` above `ahead_gt`, and `on_pace` otherwise. With no usable classification evidence the state SHALL be `unknown` with basis `none`. Exhausted status SHALL take precedence and produce `exhausted`. These states SHALL NOT change `pool_pace`, `min_pace` or routing eligibility.

#### Scenario: Running low from the last day's burn
- **WHEN** a pool had 40 percent remaining 24 hours ago, has 10 percent now, and resets in 100 hours
- **THEN** its pace is `low` with basis `burn_24h` and time to empty of 8 hours
- **AND** text output reports `running low` regardless of its weekly pace indicator

#### Scenario: On pace through reset
- **WHEN** a pool has 20 percent remaining, burns 12 percentage points per day, and resets in 24 hours
- **THEN** its pace is `on_pace` with basis `burn_24h` and leftover of 8 percentage points
- **AND** text output reports `on pace`

#### Scenario: Running rich from a monthly window
- **WHEN** a month pool has 98.5 percent remaining, burns one percentage point per day, and its cycle resets in 480 hours
- **THEN** its pace is `rich` with basis `burn_24h` and leftover of 78.5 percentage points
- **AND** text output reports `running rich`

#### Scenario: Unknown without pace evidence
- **WHEN** a pool is not exhausted and has neither usable burn evidence nor a numeric pace indicator for its window
- **THEN** its pace is `unknown` with basis `none`
- **AND** text output reports `unknown`

#### Scenario: Exhausted pool
- **WHEN** a pool has status `exhausted`
- **THEN** its pace state is `exhausted` regardless of other evidence
- **AND** text output reports `empty`

#### Scenario: Reset invalidates history burn
- **WHEN** remaining rises by more than five percentage points between samples after the history baseline
- **THEN** history-derived burn is unknown
- **AND** an available window-specific pace indicator is used with basis `pace`

### Requirement: Pool history sampling is bounded and nonblocking
The client SHALL store JSONL rows with `ts`, `pool` and `remaining` at `ROUTE_POOL_HISTORY`, defaulting to `~/.claude/route-pool-history.jsonl`. After each successful pool fetch in `route pools`, `route pick`, `route menu` and `route doctor --write`, it SHALL append one sample per pool with known remaining, except when that pool's newest sample is under 15 minutes old. It SHALL drop rows older than 72 hours when rewriting the file. A history write failure SHALL be ignored so it cannot block a pick.

#### Scenario: First successful fetch records remaining
- **WHEN** a successful pool fetch returns a pool with known remaining and no recent sample
- **THEN** the history file gains a timestamped row naming that pool and its remaining percentage

#### Scenario: Frequent reads avoid duplicate sampling
- **WHEN** the newest sample for a pool is less than 15 minutes old
- **THEN** another successful fetch does not append a sample for that pool

#### Scenario: Old history is pruned
- **WHEN** a successful fetch rewrites history containing rows older than 72 hours
- **THEN** those old rows are absent from the rewritten file

#### Scenario: History cannot be written
- **WHEN** writing the history file raises an operating-system error during `route pick`
- **THEN** the history error is ignored and routing proceeds using the fetched pool facts
