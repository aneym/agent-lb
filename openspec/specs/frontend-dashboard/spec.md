# frontend-dashboard Specification

## Purpose

Define dashboard overview, usage donut, sidebar, trend and warm-up control contracts. Entry capability: [frontend-architecture](../frontend-architecture/spec.md).

## Requirements

### Requirement: Account weekly trend planned line

The account detail usage trend SHALL include an ideal weekly remaining line when weekly reset timing is available, so operators can compare actual weekly remaining credits against the linear schedule between weekly resets.

#### Scenario: Weekly trend shows planned depletion between resets

- **WHEN** account trend buckets include weekly reset time and window length
- **THEN** the account 7-day trend includes a dashed weekly plan line computed from each bucket's reset deadline and window length

#### Scenario: Weekly trend plan restarts after reset

- **WHEN** weekly trend buckets cross into a new reset window with a new reset deadline
- **THEN** the planned line jumps back toward full remaining capacity for the new weekly window instead of continuing one global diagonal

### Requirement: Dashboard request-log list excludes deleted-account rows

When an account is deleted, request-log rows that were soft-deleted as part of that account removal MUST NOT appear in the dashboard request-log list or request-log filter-option facets.

#### Scenario: Deleted account log hidden from recent request rows

- **GIVEN** a request log row was previously associated with an account
- **AND** deleting that account soft-deleted the row
- **WHEN** a user loads `GET /api/request-logs`
- **THEN** the soft-deleted row is not included in the `requests` payload

#### Scenario: Deleted account log hidden from request-log facets

- **GIVEN** a request log row was previously associated with an account
- **AND** deleting that account soft-deleted the row
- **WHEN** a user loads `GET /api/request-logs/options`
- **THEN** the soft-deleted row does not contribute account, model, API-key, or status facet options

### Requirement: Dashboard overview metrics keep soft-deleted request logs

Dashboard overview request metrics and trends MUST continue to aggregate soft-deleted request-log rows so account deletion does not rewrite historical request activity.

#### Scenario: Deleted account log still counted in overview metrics

- **GIVEN** an account has request-log activity within the active overview timeframe
- **AND** the account is deleted afterward
- **WHEN** a user loads `GET /api/dashboard/overview`
- **THEN** request-derived metrics and trends still include that historical request-log activity

### Requirement: Dashboard projections load after the primary dashboard data

The dashboard SPA SHALL render primary dashboard content from `GET /api/dashboard/overview`
and recent request-log data without waiting for depletion or weekly-credit projection
calculations. Projection-only data, including safe-line depletion markers and weekly-credit
pace, SHALL be available from `GET /api/dashboard/projections` and fetched after overview
data is available.

#### Scenario: Main dashboard renders before projections finish

- **GIVEN** an authenticated operator opens the dashboard
- **WHEN** `GET /api/dashboard/overview` and request-log calls complete before `GET /api/dashboard/projections`
- **THEN** the dashboard renders the primary cards, usage donuts, account list, and request-log surface
- **AND** projection-only safe-line and weekly-credit fields may populate later when the projections response arrives

#### Scenario: Projection endpoint exposes heavy dashboard calculations

- **WHEN** the dashboard client requests `GET /api/dashboard/projections`
- **THEN** the response includes depletion safe-line data and weekly-credit pace data when those calculations are available
- **AND** the overview endpoint does not need to compute those fields for initial page render

### Requirement: Dashboard usage donuts present credits as stacked remaining and capacity

The dashboard's primary and secondary usage donuts MUST present remaining credits and capacity as two stacked values separated by a horizontal divider: the remaining count above (bold, `data-testid="donut-center-remaining"`) and the capacity count below (muted, `data-testid="donut-center-capacity"`). Both values MUST use locale-aware thousands separators (e.g. `7,331` and `7,560`). Compact-format abbreviation (e.g. `7.33k`) MUST NOT be used in the donut center for these panels.

The primary donut title MUST read `5-Hour Credits`. The secondary donut title MUST read `Weekly Credits`.

#### Scenario: Dashboard donut shows stacked remaining and capacity

- **WHEN** the dashboard renders a usage donut with `remaining=7331` and `total=7560`
- **THEN** the donut title reads `5-Hour Credits` or `Weekly Credits`
- **AND** the center renders `7,331` in the remaining element and `7,560` in the capacity element
- **AND** a divider separates the two values

### Requirement: API sidebar shows pooled credit bars

The APIs page left sidebar SHALL render pooled credit bars on each API key list item. Each bar SHALL display a label, percentage, and colored progress bar using the same `MiniQuotaBar` component as the Accounts sidebar.

Labels SHALL be "Pooled 5h" for the primary window and "Pooled Weekly" for the secondary window. No reset countdown text SHALL be shown.

When `pooledCapacityCreditsPrimary > 0` and `pooledRemainingPercentPrimary` is not null, the "Pooled 5h" bar SHALL be visible. Otherwise it SHALL be hidden. The "Pooled Weekly" bar SHALL be visible when `pooledRemainingPercentSecondary` is not null.

When both bars are visible, they SHALL be laid out in a 2-column grid. When only one bar is visible, it SHALL use a 1-column layout.

When API key limit rules exist, the sidebar SHALL also render the legacy limit progress bar below the pooled bars with an "API Limit" label and percentage value so it remains clearly distinct from the pooled-account bars.

#### Scenario: Both pooled bars visible

- **WHEN** an API key has both primary and secondary pooled credit data
- **THEN** the sidebar item shows "Pooled 5h" and "Pooled Weekly" bars in a 2-column grid

#### Scenario: Primary bar hidden for free-tier accounts

- **WHEN** an API key's pooled primary capacity is 0
- **THEN** only the "Pooled Weekly" bar is shown in a 1-column layout

#### Scenario: No credit data hides bars

- **WHEN** an API key has no pooled credit data
- **THEN** no credit bars are rendered on that list item

#### Scenario: API limit bar is labeled distinctly

- **WHEN** an API key has configured limit rules
- **THEN** the sidebar renders the legacy limit bar with an "API Limit" label below the pooled bars

### Requirement: Footer version update indicator

The dashboard footer SHALL show the running application version and SHALL display a compact update-available icon next to that version only when the runtime version API confirms a newer stable GitHub release exists.

#### Scenario: Newer release is available

- **WHEN** `GET /api/runtime/version` returns `updateAvailable: true` with a `latestVersion`
- **THEN** the footer renders an accessible update icon beside the current version
- **AND** the icon links to `https://github.com/aneym/agent-lb/releases/latest`
- **AND** the icon title or accessible label includes the latest version

#### Scenario: Version lookup is unavailable

- **WHEN** `GET /api/runtime/version` fails or returns no newer version
- **THEN** the footer continues showing the current version without an update indicator

### Requirement: Dashboard limit warm-up controls

The dashboard SHALL expose global limit warm-up controls in Settings and per-account opt-in/status in account views. The global default SHALL be disabled.

#### Scenario: Configure warm-up behavior
- **WHEN** an operator opens Settings
- **THEN** the dashboard shows controls for enabling limit warm-up, selecting primary/secondary/both windows, setting the warm-up model, setting the prompt, and setting the cooldown

#### Scenario: Validate warm-up settings before save
- **WHEN** an operator edits warm-up model, prompt, or cooldown fields
- **THEN** the dashboard enforces the same non-empty, max-length, and integer cooldown bounds as the backend API before enabling save

#### Scenario: Show per-account opt-in and last attempt
- **WHEN** account summaries include limit warm-up status
- **THEN** the dashboard shows whether warm-up is enabled for that account
- **AND** it shows the latest attempt window, status, model, and completion/attempt time when available

#### Scenario: Warm-up controls are accessible by name
- **WHEN** an operator navigates the dashboard with assistive technology
- **THEN** global and per-account warm-up toggles expose descriptive accessible names that identify the setting and account context

### Requirement: APIs tab shows a 7-day account-cost donut for selected API keys

When the selected API key's 7-day usage payload contains one or more `accountCosts[]` items, the APIs tab detail panel SHALL render the account-cost donut section and usage-trend section inside a single shared card. On large screens, the split layout SHALL use a 25:75 width ratio with the donut on the left, the trend on the right, and a vertical separator between them.

The donut section SHALL include a title and subtitle, SHALL show the 7-day total cost in the donut center, SHALL not render a separate `Total $...` summary in the section header, and SHALL render the legend below the donut.

#### Scenario: Donut renders inside the shared usage card
- **WHEN** a selected API key has 7-day account-cost data and trend data
- **THEN** the detail panel renders the account-cost donut section to the left of the trend section inside one shared card
- **AND** the large-screen layout uses a 25:75 split with a vertical separator between the sections

#### Scenario: Donut is omitted when no account-cost buckets exist
- **WHEN** the selected API key's `usage-7d.accountCosts[]` array is empty
- **THEN** the APIs tab does not render the account-cost donut card

### Requirement: APIs tab account-cost donut uses existing account labels and privacy rules

The donut legend SHALL use the account label derived from the existing payload fields: `Deleted Account` for `isDeleted: true`, otherwise the account `email` when present, otherwise `Unknown Account`. Non-deleted account labels MUST respect the hide-account-info privacy setting used elsewhere in the dashboard.

The legend SHALL show each visible bucket's 7-day cost, SHALL coordinate hover highlighting with the matching pie slice, and SHALL use the same vertically scrollable five-row viewport pattern as the dashboard donuts when more rows exist than fit without scrolling.

#### Scenario: Deleted account label is explicit
- **WHEN** an `accountCosts[]` item has `isDeleted: true`
- **THEN** the legend label is `Deleted Account`

#### Scenario: Privacy hiding applies to non-deleted account labels
- **WHEN** the hide-account-info setting is enabled
- **AND** a visible donut legend row represents a non-deleted account label
- **THEN** the label text is privacy-blurred

#### Scenario: Legend scroll viewport matches dashboard donuts
- **WHEN** more than five account-cost buckets are present
- **THEN** the donut legend keeps all rows available
- **AND** the visible legend viewport shows five rows before scrolling

### Requirement: APIs tab account-cost donut follows the dashboard donut visual system

The account-cost donut SHALL use the same sizing, palette generation, reduced-motion behavior, hover-linked legend highlighting, and gray consumed/deleted color treatment as the dashboard donut visual system.

#### Scenario: Deleted-account slice uses the consumed gray color
- **WHEN** the donut renders a deleted-account bucket
- **THEN** that bucket uses the same gray color family used by the dashboard donut's consumed or used segment

### Requirement: APIs tab usage trend control layout is compact in the split view

The APIs tab usage trend card SHALL keep its heading and subtitle, SHALL align the accumulated toggle and Tokens/Cost legend to the right side of the heading block on larger screens, and SHALL reduce the chart right margin to fit the split layout.

#### Scenario: Usage trend controls align with the heading row
- **WHEN** the usage trend card renders
- **THEN** the Tokens/Cost legend appears to the right of the heading block on larger screens
- **AND** the accumulated toggle remains in the same right-side controls group

#### Scenario: Usage trend uses compact right margin
- **WHEN** the usage trend chart renders in the split APIs-tab layout
- **THEN** the chart right margin is reduced from the previous wider layout to a compact right margin

### Requirement: Dashboard account summaries sorted by primary capacity

The dashboard overview API MUST return account summaries sorted by `capacity_credits_primary` in descending order so the highest-capacity accounts appear first. Accounts with no primary capacity MUST sort after accounts that have one.

#### Scenario: Accounts ordered by primary capacity

- **WHEN** the dashboard overview response includes multiple accounts with different `capacity_credits_primary` values
- **THEN** accounts are ordered from highest to lowest primary capacity

#### Scenario: Accounts without primary capacity sort last

- **WHEN** an account has `capacity_credits_primary` of `null` or `0`
- **THEN** that account appears after accounts with a positive primary capacity

### Requirement: Account card row height is 11.5rem

The dashboard account card viewport MUST use 11.5rem per visible row.

#### Scenario: Account card max height

- **WHEN** the account cards container renders with `ACCOUNT_CARD_VISIBLE_ROWS=2`
- **THEN** the container `maxHeight` is `calc(2 * 11.5rem + 1rem)`

### Requirement: Weekly credits pace header uses flex-start alignment

The weekly credits pace card header MUST align the title and gauge icon to the flex start, not vertically centered.

#### Scenario: Header alignment

- **WHEN** the weekly credits pace card renders
- **THEN** the header row uses `justify-between` without `items-center`
