## MODIFIED Requirements

### Requirement: Pool metrics strip shows the plan value multiple

The menubar pool metrics strip MUST show, when computable, a value-multiple
line: the API-equivalent retail value of the tokens burned in the weekly
window divided by the prorated weekly cost of the plans backing the pool.
The multiple MUST be visually emphasized by weight only (never color), and
the line MUST be prefixed `≈` whenever any counted account's plan price came
from the client-side list-price fallback rather than an operator-entered
amount. Both the numerator and the denominator MUST be scoped to the active
provider scope (§9.2/§13): when a single provider is selected, only that
provider's headline-countable accounts and their attributed usage value
count toward the line.

#### Scenario: Multiple renders with the correct math (all-scope)

- **GIVEN** the provider scope is All, a pool of 3 headline-countable
  accounts — two Claude Max priced from the list-price table at $200/mo
  each, one Codex Pro with an operator-entered `subscription.amount` of
  $200 (USD) — and pool-global `summary.cost.totalUsd7d` of $6,200
- **WHEN** the pool metrics strip renders
- **THEN** the weekly plan cost is `(200 + 200 + 200) * 7 / 30.4375 ≈ $137.99`
- **AND** the multiple is `6200 / 137.99 ≈ 44.9`
- **AND** the line renders `≈45× value · $6.2k vs $138/wk` (estimated prefix
  present because two of the three accounts used the fallback table)

#### Scenario: Multiple is scoped to the active provider

- **GIVEN** the provider scope is Claude, the pool has 2 Claude Max accounts
  (fallback-priced, $200/mo each) and 1 Codex Pro account (operator-priced),
  and a scoped `/api/usage/summary?provider=anthropic` response with
  `cost.totalUsd7d` of $2,000
- **WHEN** the value-multiple line renders
- **THEN** the denominator sums only the 2 Claude accounts'
  `weeklyPlanCost ≈ $91.99`
- **AND** the numerator is the scoped `$2,000`, giving a multiple of
  `≈21.7×`, distinct from the all-scope multiple
- **AND** the Codex account does not contribute to either side of the ratio

#### Scenario: Fully operator-priced pool omits the estimate marker

- **GIVEN** every counted account (within the active scope) has an
  operator-entered USD `subscription.amount`
- **WHEN** the multiple is computed
- **THEN** the line has no `≈` prefix

#### Scenario: Line is absent when unpriceable

- **WHEN**, within the active scope, `summary.cost.totalUsd7d` is nil or
  ≤ 0, OR no headline-countable account in scope has a resolvable monthly
  price (operator amount or list-price table)
- **THEN** the value-multiple line does not render and contributes zero
  lines to the metrics strip

#### Scenario: Denominator excludes unusable accounts

- **GIVEN** an account that is subscription-canceled, deactivated, or
  `reauth_required` (i.e. not `isHeadlineCountable`)
- **WHEN** the weekly plan cost is summed
- **THEN** that account's plan price is excluded from the denominator
  regardless of what plan type it has on file, and regardless of provider
  scope

## ADDED Requirements

### Requirement: Provider scope selection scopes all downstream stats

Selecting a provider scope (All/Codex/Claude) MUST re-derive every stat
downstream of it from a summary fetched for that scope: the metrics strip
(cost/requests/errors, tokens/cached), the value-multiple line, and the
menu-bar status icon percents. A summary fetch issued under a since-changed
scope MUST NOT overwrite state for the currently-selected scope.

#### Scenario: Metrics strip re-fetches on scope change

- **GIVEN** the panel is showing All-scope metrics
- **WHEN** the user selects the Claude segment
- **THEN** the client issues `GET /api/usage/summary?provider=anthropic`
- **AND** the metrics strip, value-multiple line, and status icon all update
  from that response once it resolves
- **AND** the `· all providers` disclosure tag is not shown when scope is a
  single provider (the numbers genuinely are scoped)

#### Scenario: Stale scoped fetch is discarded

- **GIVEN** the user selects Claude (fetch A starts), then quickly selects
  Codex (fetch B starts) before fetch A resolves
- **WHEN** fetch A resolves after fetch B has already started
- **THEN** fetch A's response is discarded and never applied to app state
- **AND** when fetch B resolves, its response is applied normally

#### Scenario: Status icon percents follow the active scope

- **GIVEN** the provider scope is Codex
- **WHEN** the status-bar icon is drawn
- **THEN** `AppState.statusIconPercents(from:)` derives its percents from
  the Codex-scoped summary, not a pool-global one

#### Scenario: Privacy pseudonyms are unaffected by scope changes

- **GIVEN** privacy mode is enabled
- **WHEN** the provider scope changes
- **THEN** account pseudonyms are unchanged (built once from the full
  account list per the existing privacy-mode requirement, independent of
  provider scope)

### Requirement: Status icon renders two concentric rings

The menu-bar status icon MUST render the primary (5-hour) remaining
percentage as an outer ring and the secondary (weekly, monthly fallback)
remaining percentage as an inner ring, both monochrome and following the
active provider scope. Unknown state draws both rings track-only. Risk
state draws the outer ring plus the exclamation glyph and omits the inner
ring. Down and update treatments are unchanged.

#### Scenario: Normal state draws both rings

- **GIVEN** `primaryPercent = 62`, `longWindowPercent = 51`
- **WHEN** `StatusIconRenderer.icon(for:primaryPercent:longWindowPercent:)`
  is called with a normal `IconState`
- **THEN** the outer ring fills to 62% and the inner ring fills to 51%,
  both monochrome (no color-coded state)

#### Scenario: Unknown state draws track-only rings

- **GIVEN** `primaryPercent` and `longWindowPercent` are both nil
- **WHEN** the icon is rendered in the unknown state
- **THEN** both the outer and inner rings render as empty tracks (no fill
  arc), never implying 0% remaining

#### Scenario: Risk state omits the inner ring

- **GIVEN** the icon state is risk (a window is nearing exhaustion)
- **WHEN** the icon is rendered
- **THEN** the outer ring renders with its fill percentage plus the
  exclamation glyph overlay
- **AND** the inner ring is omitted entirely (not drawn track-only, not
  drawn filled)

#### Scenario: Down and update treatments are unchanged

- **GIVEN** the icon state is down or update-available
- **WHEN** the icon is rendered
- **THEN** the existing down/update glyph treatment renders exactly as
  before this change, with no ring changes

#### Scenario: Icon follows the active provider scope

- **GIVEN** the provider scope is Claude
- **WHEN** the status icon percents are computed
- **THEN** they are derived from the Claude-scoped summary, matching the
  metrics strip and value-multiple line in the open panel

### Requirement: Icon-only chrome buttons render without a circle chip

Icon-only chrome buttons MUST render as a plain template glyph with no
circular glass chip background: the header eye/eye.slash privacy toggle,
header refresh, header overflow menu trigger, and footer power/quit button.
Each MUST preserve a 22×22 pt hit target and its existing accessibility
label. Buttons that display visible text keep their existing chip
treatment; this requirement applies only to icon-only affordances.

#### Scenario: Icon-only buttons have no chip background

- **GIVEN** the header and footer chrome
- **WHEN** the eye/eye.slash, refresh, overflow, and power buttons render
- **THEN** none of them draw a circular glass/material chip behind the
  glyph — only the flat template-rendered SF Symbol is visible

#### Scenario: Hit target and accessibility label are preserved

- **GIVEN** any icon-only chrome button after the chip is removed
- **WHEN** its tappable frame and accessibility properties are inspected
- **THEN** the hit target remains 22×22 pt and the accessibility label text
  is unchanged from before this change

#### Scenario: Text-carrying buttons are unaffected

- **GIVEN** a button that displays visible text (e.g. the provider scope
  segmented control's segments)
- **WHEN** it renders
- **THEN** its existing chip/fill treatment is unchanged by this
  requirement
