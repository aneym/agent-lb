## ADDED Requirements

### Requirement: Cursor pool cooldown isolation
Cursor limit outcomes SHALL cool only the first matching configured model pool, defaulting to cursor-other. Monthly reset dates in vendor errors SHALL expire at midnight UTC and SHALL be recorded as vendor limit evidence. Account-wide auth cooldowns and legacy cooldowns SHALL block every pool. Account selection SHALL respect both scopes, including explicitly pinned accounts. Account listings SHALL expose live per-pool cooldowns.

#### Scenario: Other models limit leaves Composer ready
- **WHEN** Sonnet hits a monthly limit ending 10/30/2026 on a single Cursor account
- **THEN** Composer runs successfully, another Sonnet run does not spawn, and cursor-other has a cooldown ending 2026-10-30T00:00:00Z without an account-wide cooldown

#### Scenario: Authentication blocks every model
- **WHEN** a Cursor run returns an authentication failure
- **THEN** an account-wide cooldown is recorded and Composer cannot run

### Requirement: Cursor budget provenance and eligibility
Budget pools SHALL count enabled authenticated accounts not cooling account-wide or on that pool. PoolSummary SHALL expose camelCase percentUsed and percentSource. Current-cycle live vendor limit evidence on a budgeted account SHALL report 100 percent with vendor provenance; otherwise budgeted observed spend SHALL supply a capped one-decimal estimate. Unknown budgets SHALL report null provenance and percent. Ultra estimates SHALL be calibrated to the owner's 2026-09-30 dashboard using the service's cost calculation; other tier budgets SHALL remain unchanged.

#### Scenario: Vendor provenance supersedes the estimate
- **WHEN** a budgeted plan has a live current-cycle vendor limit
- **THEN** percentUsed is 100 and percentSource is vendor regardless of its observed token estimate

### Requirement: Shared Cursor subscription plans
The seat registry SHALL accept an optional plan id through `seat set --plan`, mirror it into state, and expose it in account listings and the API. Cursor identities with the same plan SHALL share pool cooldowns and vendor limit evidence, but not authentication failures. Plan spend SHALL sum every member's usage and count its tier budget once. Accounts without a plan SHALL retain independent accounting. Ultra calibration SHALL use the combined cursor-main and cursor-gmail plan tally.

#### Scenario: Two identities share one plan limit
- **WHEN** the first identity on a shared Ultra plan hits the Sonnet monthly limit
- **THEN** neither identity spawns another Sonnet run, Composer still runs, and Other models reports two accounts, zero eligible accounts, vendor 100 percent usage and one Ultra budget

#### Scenario: Pools report independent capacity
- **WHEN** only cursor-other has a live vendor limit
- **THEN** cursor-models remains eligible while cursor-other reports zero eligible accounts and 100 percent vendor usage
