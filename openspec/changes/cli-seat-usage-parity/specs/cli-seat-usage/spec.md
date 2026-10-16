## ADDED Requirements

### Requirement: Seat quota is collected from each vendor's own usage source
`seat quota` SHALL read per-account quota from Cursor DashboardService (`GetCurrentPeriodUsage`, `GetPlanInfo`), Devin SeatManagementService (`GetUserStatus`), and Muse `SubscriptionUsage` frames observed in seat runs. It SHALL store each reading in seat state with its source and observation time, and SHALL print no credential.

#### Scenario: Cursor vendor reading
- **WHEN** Cursor returns `autoPercentUsed` 100, `apiPercentUsed` 40 and a `billingCycleEnd`
- **THEN** the account's quota has window `cursor-models` used 100, window `cursor-other` used 40, both of kind `monthly`, reset at the cycle end and source `vendor`

#### Scenario: Devin reading hides the daily window
- **WHEN** `GetUserStatus` reports `hideDailyQuota` true and `weeklyQuotaRemainingPercent` 70
- **THEN** the quota has only window `weekly`, used 30, and no `daily` window

#### Scenario: Muse reports nothing
- **WHEN** no Muse seat run has carried a `SubscriptionUsage` frame
- **THEN** Muse quota has source `unavailable`, reason `not_reported` and no windows

#### Scenario: Muse frame observed in a run
- **WHEN** a Muse seat run's JSONL carries a usage frame with a 300-minute window used 12 and weekly used 30
- **THEN** quota has windows `session` (duration 300, used 12) and `weekly` (used 30), source `vendor_observed`, and `observedAt` equal to the frame's `observedAtMs`

### Requirement: Quota collection respects the login floor
`seat quota` SHALL NOT call Muse `/muse-code/key`, read Alex's Muse keychain item or start a Muse host only to read usage. A credential read that needs user interaction SHALL fail as `credential_unavailable` and SHALL NOT show a dialog.

#### Scenario: Keychain needs interaction
- **WHEN** the Cursor login credential read would prompt
- **THEN** the account's quota is `unavailable` with reason `credential_unavailable` and no dialog appears

### Requirement: Seat quota refreshes on a schedule and after runs
The server SHALL run `seat quota` every 300 seconds by default, single-flight with a 60-second timeout, and each `seat run` SHALL refresh the quota of the account it used. A failed refresh SHALL keep the previous reading and its original `observedAt`. A run's exit code SHALL NOT depend on the refresh.

#### Scenario: Vendor error keeps the last reading
- **WHEN** a refresh gets HTTP 500 from Cursor after a reading taken at 10:00 ET
- **THEN** the account keeps that reading, `observedAt` stays 10:00 ET, and `stale` becomes true after 15 minutes

### Requirement: Seat accounts expose quota, status and today's usage
`GET /api/pools/seat-accounts` SHALL return for each account `quota`, `status` in the proxy account vocabulary, `statusResetAt`, `planLabel`, `email` and `today`, with the fields named in design.md. Unknown tokens or value SHALL be null, never 0. Cursor accounts without a vendor reading SHALL carry the list-price estimate with source `estimate`.

#### Scenario: Unknown Muse tokens
- **WHEN** a Muse run recorded null tokens
- **THEN** `today.tokensKnown` is false and `today.tokensIn` is null

#### Scenario: Cursor pool cooldown
- **WHEN** a Cursor account has a live `cursor-other` cooldown until Oct 30 00:00 UTC
- **THEN** its status is `rate_limited` and `statusResetAt` is that moment

### Requirement: CLI-seat pools expose measured windows without changing routing
`GET /api/pools` SHALL add `windows` and `quotaSource` to CLI-seat pools, computed from measured account quota. Plan-shared Cursor accounts SHALL count once. `headroomPercent`, `aggregateRemainingPercent`, `eligibleAccounts`, `status` and seat selection SHALL keep their existing semantics.

#### Scenario: Muse with no reading
- **WHEN** no Muse account has a quota reading
- **THEN** the Muse pool's `windows` is empty, `quotaSource` is `unavailable` and `headroomPercent` stays null

### Requirement: Usage summary counts seat activity per provider
`GET /api/usage/summary?provider=P` SHALL echo `provider` and SHALL return `seatActivity` for the last 168 hours from seat state, or null when P has no seat accounts. Seat runs and request-log requests SHALL stay separate counts.

#### Scenario: Cursor scope with Grok runs
- **WHEN** Cursor ran 4 `grok-4` runs and 2 `composer-1` runs this week
- **THEN** `seatActivity.runs` is 6 and `models` lists `grok-4` with 4 runs

### Requirement: Route pools prints server values for Muse
`route pools` SHALL print the Muse pool from the server response and SHALL NOT synthesize headroom or zero tokens.

#### Scenario: Server omits Muse headroom
- **WHEN** the Muse pool's `headroomPercent` is null
- **THEN** `route pools` prints `-` for it
