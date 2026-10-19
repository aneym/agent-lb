Related sections: [Product and visual design](design.md) · [Architecture](architecture.md) · [Build, verification, and scope](build-and-verification.md) · [Remote mode and refinements](remote-mode-and-refinements.md).

## 7. Remote Mode (Tailscale)

The app must run on machines other than the service host (e.g. a MacBook
reaching this Mac Studio's agent-lb over Tailscale).

- **Base URL is configurable, no UI:** `APIClient` reads
  `UserDefaults.standard.string(forKey: "baseURL")` at init, falling back to
  `http://127.0.0.1:2455`. Set per-machine via
  `defaults write com.aneyman.agentlb.menubar baseURL "http://studio:2455"`.
  All endpoints, the Dashboard button, and Copy URL derive from this base.
- **Remote detection:** `isRemote = !["127.0.0.1", "localhost", "::1"].contains(baseURL.host)`.
- **Remote rendering rules:**
  - Header sub-line shows the host (`studio · synced 12s ago`).
  - All launchctl-backed controls (Start/Stop/Restart, power menu items) are
    hidden — launchctl only works on the service host. The footer shows
    Dashboard / Copy URL only; the overflow menu keeps Launch at Login + Quit.
  - Service-down empty state drops the Start button; copy becomes
    "Can't reach Agent LB at <host>" with a Retry button (forces refresh).
  - `ServiceController.isLoadedWithPID()` is skipped when remote; `/health`
    unreachable maps directly to `.unreachable` (never `.stopped`).

---

## 8. v1.1 Visual Refresh (post-screenshot review)

User feedback on v1.0: prettier, larger, more detailed, more elegant; status
icon becomes a usage ring gauge; account filtering parity with the dashboard.

### 8.1 Status-bar icon → ring gauge

Replace all glyph composites with a **ring gauge** (18×18 template NSImage):
- Track: full circle, 2.5 pt stroke, 22% alpha.
- Fill arc: `primaryRemainingPercent` of the pool (from `/api/usage/summary`),
  full alpha, rounded caps, starting 12 o'clock, clockwise.
- States: risk danger/critical → bold 7 pt `exclamationmark` centered in the
  ring; service down → ring at 35% alpha + diagonal slash; update available →
  3 pt dot at top-right (punched hole halo); unknown percent → track only.
- Cache key: state + percent bucketed to 4% steps. Closed-state poller must
  also fetch `/api/usage/summary` on its 120 s tick to drive the arc.

### 8.2 Popover (width 380 pt)

**Header (fixed, glass chrome, exactly two 0-wrap lines)**
- L1: ring StatusGlyph + `Agent LB` (15 pt semibold) · spacer · ⟳ and ⋯ as
  28 pt circular glass icon buttons (`.menuIndicator(.hidden)`, no chevron).
- L2: host chip — SHORT host only (`studio`, FQDN truncated at first dot) in a
  2 pt-radius capsule (.thinMaterial, 10 pt mono) — `synced 1s` · spacer ·
  `v1.20.0-beta.3` (11 pt, .tertiary). Remote mode: chip shows the host;
  local mode: chip says `local`.

**Pool cards** — each window card gains a 30 pt ring gauge (3 pt stroke, same
geometry as the status icon) left of the percent (26 pt mono semibold);
below: `7.5k / 7.8k cr` (remaining/capacity credits, compact), reset
countdown, risk/pace line (unchanged emphasis rules). Metrics strip becomes
two 11 pt mono lines:
`$6,165.06 · 7d   63.2k req   err 0.67%`
`3.8B tok · 7d    80% cached` (from `tokensSecondaryWindow`,
`cachedTokensSecondaryWindow`; omit line if fields nil).

**Accounts — filtering (dashboard parity, adapted)**
- Header row: `ACCOUNTS (n)` shows the FILTERED count · spacer · search
  toggle (magnifier, 22 pt) · filter Menu (`line.3.horizontal.decrease.circle`,
  fills/bolds when any filter active).
- Provider chips row under the header: `All n` / `Anthropic n` / `OpenAI n`
  capsules (10 pt, counts live with other filters applied; selected chip =
  `.primary` fill with inverted label; hide row when only one provider
  exists). Matches dashboard `AccountFilterState.provider`.
- Filter Menu: Status — All / Active / Rate-limited / Paused / Inactive
  (single-select, mirrors dashboard status filter; Rate-limited = synthetic:
  `rateLimitResetAt` in future). Sort — Reset (soonest) [default] /
  Reset (latest) / Name A-Z / Name Z-A (subset of dashboard sort modes;
  `subscription_soonest` omitted — field not in API response).
- Search: toggling the magnifier reveals an inline 24 pt TextField row
  (`Search accounts…`, filters displayName/email/alias substring,
  case-insensitive); Esc or empty+blur hides it. Session-only @State;
  sort persists via @AppStorage("accountSort").
- Empty filtered result: `No accounts match` + `Clear filters` inline button.

**Account rows (48 pt, more detail)**
- Leading: 20 pt mini ring gauge of `primaryRemainingPercent` (track 22%,
  2 pt stroke) replacing the bar; the status glyph moves INTO the ring center
  at 8 pt (pause/xmark only when not active; active shows no center glyph).
- L1: displayName (13 pt) + duplicate-email suffix rule unchanged · spacer ·
  `62%` (13 pt mono semibold). Rate-limited: `limited · 18:20` bold replaces
  percent.
- L2 (11 pt secondary): planType chip (capsule, 9 pt caps) · `weekly 51%` ·
  `resets 23:50` · when deactivated: reason summary.
- Context menu unchanged (pause/reactivate/copy/open dashboard).

**Recent** — rows gain relative age (`2m`, trailing, .tertiary, mono).
**Footer** — unchanged structure; buttons `.controlSize(.regular)` (slightly
larger), hairline divider above.

### 8.3 Layout & motion
- Width 380 pt; panel max height 600 pt; paddings: panel 14 pt, card inner
  12 pt, section gap 12 pt. All other §2 rules (monochrome, glass-on-chrome
  only, 150 ms easeOut) unchanged.
- Ring gauges animate arc changes with the same 150 ms easeOut.

---

## 9. v1.2 — Top-level provider scope, separated windows, larger panel

User feedback on v1.1: stats feel mixed together; needs full visibility —
either denser or a larger window; 5-hour and weekly limits must read as
clearly separate; the provider filter must be TOP-LEVEL and scope all stats,
exactly like the dashboard.

### 9.1 Panel
- Width **460 pt**, max height **720 pt**. Accounts list shows up to 8 rows
  before scrolling. Everything else §8.3.

### 9.2 Top-level provider scope (dashboard parity)
- A segmented control directly under the header, full-width, in the chrome
  area: **All n / Codex n / Claude n** (labels per dashboard `providerLabel`:
  openai → "Codex", anthropic → "Claude"; live counts). Selected segment =
  ink fill (`.primary` background, inverted label); unselected = plain.
  Session-persisted via `@AppStorage("providerScope")`.
- Scope applies to EVERYTHING below it:
  - **Pool windows**: scope == all → use `/api/usage/summary` verbatim.
    Scoped → recompute from the filtered accounts (dashboard
    `summarizeFilteredWindow` semantics): percent = Σ`remainingCredits*` /
    Σ`capacityCredits*` × 100 per window, credits lines from the same sums,
    resetAt = earliest FUTURE `resetAt*` among scoped accounts. Requires
    decoding the per-account credit fields already present in the accounts
    response (`remainingCreditsPrimary`, `capacityCreditsPrimary`,
    `remainingCreditsSecondary`, `capacityCreditsSecondary`).
  - **Accounts list**: filtered to scope (provider chips REMOVED from the
    accounts section — superseded by this control; status/sort menu and
    search stay in the accounts header).
  - **Recent**: entries filtered to accountIds within scope.
- Honesty rules when scoped: the 7-day metrics strip (cost/req/err/tokens —
  global numbers from `/api/usage/summary`) gains a trailing `· all
  providers` tag (9 pt, .tertiary); risk/pace lines are HIDDEN when scoped
  (projections are pool-global; do not mislead). Status-bar icon stays
  global (pool-wide) regardless of scope.
- Pure scoped-window math lives in `AccountFilter.swift` (or a sibling
  `ProviderScope.swift`) as testable functions; unit tests against the
  accounts fixture assert Σ math, earliest-future-reset, and empty-scope
  behavior (scope with zero accounts → windows render `—`).

### 9.3 Windows separated everywhere
- Pool cards: keep the two separate cards, titles become `5-HOUR LIMIT` and
  `WEEKLY LIMIT`; each gains a small `n accts` count (scoped) at top-right.
- **Account rows** (the v1.1 ring+subline mixed the two windows — fix):
  line 2 becomes a two-column grid, one column per window, each
  self-contained and labeled:
  `5H  [meter] 62% · 0:15`   |   `WK  [meter] 51% · 23h`
  (label 9 pt caps .secondary, 56 pt MonoMeter bar, mono percent, compact
  countdown to that window's reset; `—` for missing windows). The leading
  20 pt ring gauge stays (5-hour, matches the status icon); rate-limited /
  paused / deactivated trailing states unchanged. Row height 52 pt.
- Remove `weekly NN%` from any subline — the grid is now the only place
  per-window numbers appear in a row.

---

## 10. Pool reset semantics — "next reset" (user feedback)

Problem: a scoped pool card shows a SUMMED percentage (e.g. 101/400 cr across
4 accounts) but each account has its own staggered reset clock — the
aggregate never "resets" at one moment. `resets in 1h 29m` next to a summed
number wrongly implies full-pool replenishment.

Required change (both pool cards, all scopes):
- Label becomes `next reset in <countdown> · +<n> cr` where `+n` =
  Σ(capacity − remaining) over the scoped accounts whose window resets at
  that earliest future time (1-minute tolerance bucket). Omit `· +n cr`
  when credits are unknown (nil fields) or scope==all-with-server-summary
  (server gives no per-reset recovery; show plain `next reset in <countdown>`).
- `ProviderScope.summarizeWindow` returns the extra `recoveredCredits` value;
  pure + unit-tested against the fixture (staggered resets → only the
  earliest bucket's recovery; same-minute resets sum; single-account scope →
  recovery == capacity − remaining).
- Card hover (`.help`) lists the per-account schedule, soonest first:
  `<displayName> · <HH:mm> · +<n> cr` one per line.
- Account rows unchanged (their per-window countdowns are unambiguous).

---

## 11. Value multiple (pool metrics strip)

Problem: the metrics strip shows raw cost/token numbers with no sense of
what the pool is *worth* — the API-equivalent value of a week's usage versus
what the underlying flat-rate subscriptions cost for the same week.

- New line in the metrics strip (`PoolSection.swift`), rendered only when
  computable: bold `N×` (weight only, `.primary`; monochrome per §2.3) +
  secondary mono `value · $X vs $Y/wk`. `N = totalUsd7d ÷ weeklyPlanCost`.
- `weeklyPlanCost = (Σ monthly price over isHeadlineCountable accounts) × 7 ÷
  30.4375`. Monthly price per account: operator-entered `subscription.amount`
  when USD and > 0, else a client-side list-price table (`PlanPricing.swift`)
  keyed on (provider, planType); per-seat/unknown plans are excluded from the
  sum rather than guessed at. Any account that used the table prefixes the
  whole multiple `≈` (estimated).
- ~~Pool-global by construction, regardless of the §9.2 provider scope: the
  numerator (`totalUsd7d`) is an all-providers server figure, so scoping the
  denominator alone would misrepresent the ratio.~~ **Superseded by §13**:
  the owner's direction is that provider-scope selection now scopes the
  value multiple too — both numerator and denominator are computed from a
  provider-scoped `/api/usage/summary` fetch. This entry is kept as a record
  of the original decision and its now-outdated rationale.
- Nil (line absent) when `totalUsd7d` is ≤ 0 or no account resolves a price —
  never a `0×`/`NaN×` render.
- Full breakdown (raw dollars, plan count, monthly total, estimate note) in a
  `.help()` tooltip; the strip itself stays compact.
- Layout-deterministic like everything else in this doc: `PanelLayout`'s
  `metricsLines` input becomes `1 (cost) + tokenLine? + valueLine?`, a fixed
  line count driving `PanelMetrics.metricsLine` height — never measured.
- Copy guardrail: on-screen strings say "value", never "arbitrage" /
  "pooling" / "reselling" / "circumvent" (see change `context.md`); the
  internal type name `ArbitrageStats` is fine, the UI text is not.

## 12. Privacy mode

Problem: the panel is worth sharing (§11 makes it more so), but every
account row shows a real email and the header shows the operator's real
remote hostname.

- `@AppStorage("privacyMode")`, toggled by a header eye/eye.slash glass
  button (next to refresh) and mirrored in the overflow menu as "Hide
  Sensitive Info" — same key, either affordance flips both.
- Redacts ONLY identity text: account display names + duplicate-tag suffixes
  → stable pseudonyms (`"Claude 1"`, `"Codex 2"`); remote host chip →
  `"remote"`; account names inside the §10 pool-card tooltip → pseudonyms.
  Never redacts aggregate numbers (percentages, cost, tokens, the §11
  multiple) — those are the point of sharing.
- Pseudonyms are built once in `RootView` from the FULL account list
  (`PrivacyMask.build`), numbered per-provider in `accountId`-sorted order,
  and exposed via `EnvironmentValues.privacyMask`. Keying on a fixed sort of
  the full list — not row position or the scoped/visible subset — keeps a
  given account's label stable across refresh, re-sort, and provider-scope
  changes.
- Layout-neutral: redacted strings render inside the same fixed-height
  row/line frames as real text (truncate, don't reflow); privacy mode is not
  a `PanelLayout.Inputs` field, so toggling it never changes panel height —
  same determinism invariant as §11.

## 13. Scoped stats + two-ring status icon

Problem: §9.2 scoped pool windows/accounts/Recent to the provider filter but
deliberately left §11's value multiple and the status icon pool-global. The
owner's direction reverses that call — scope selection should scope
*everything*. Separately, the icon has only ever shown the 5-hour window,
and icon-only chrome buttons carry a chip that's inconsistent with the rest
of the flat SF-Symbol design language.

- `GET /api/usage/summary` gains an optional `?provider=<name>` param
  (server-side, since the client cannot attribute `totalUsd7d`/errors/tokens
  to a provider on its own). Scoped responses filter to that provider's
  subscription-usable accounts and drop unattributed (`account_id IS NULL`)
  logs; an unknown provider yields a valid empty summary; no-param behavior
  is unchanged.
- Selecting a §9.2 scope now re-fetches the summary with that param and
  re-derives the metrics strip, the §11 value multiple (**both** numerator
  and denominator scoped — supersedes §11's "pool-global by construction"
  bullet), and the status-icon percents from the scoped response. The `· all
  providers` disclosure tag no longer applies when scope is a single
  provider. A stale-fetch guard drops any summary response resolved after
  the scope has since changed, so an in-flight All-scope fetch can never
  clobber a since-selected Claude/Codex scope.
- §12 privacy pseudonyms are explicitly unaffected — still keyed on the full
  account list, independent of scope, as already specified.
- Status-bar icon becomes two concentric monochrome rings: outer = primary
  (5-hour) remaining %, inner = secondary (weekly, monthly fallback)
  remaining %. `StatusIconRenderer.icon(for:primaryPercent:
  longWindowPercent:)` replaces the old single-percent signature. Unknown
  state draws both rings track-only. Risk state draws the outer ring plus
  the exclamation glyph and omits the inner ring — two thin rings plus the
  glyph was illegible at menu-bar size, so the tradeoff favors the
  higher-priority risk signal over the extra data point. Down/update
  treatments are unchanged. The icon follows the active provider scope like
  the rest of the panel.
- Icon-only chrome buttons (header eye/refresh/overflow, footer power) lose
  their circular glass chip — plain template glyph, matching the flat
  SF-Symbol language used elsewhere (§2.3, §8) — while keeping a 22×22 pt
  hit target and their existing accessibility label. Text-carrying buttons
  (e.g. the scope segmented control) are unaffected; this is an icon-only
  affordance change.
