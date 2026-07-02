## Why

`menubar-privacy-and-arbitrage` shipped the §9.2 provider scope as a filter
over pool windows, the accounts list, and Recent — but the metrics strip
(cost/req/err/tokens), the §11 value multiple, and the status-bar icon
stayed pool-global "by construction" (§11's own words). The owner's
direction for this cycle reverses that call: selecting Codex or Claude
should scope *everything* the panel shows, including the value multiple and
the icon itself — matching how the dashboard already behaves. Separately,
the status-bar icon has only ever shown one window (5-hour); the weekly
window is invisible until the panel is opened, and the icon-only chrome
buttons (eye, refresh, overflow, footer power) carry glass circle chips that
read heavier than the flat SF Symbols used everywhere else in the design.

## What Changes

- **Server**: `GET /api/usage/summary` gains an optional `?provider=<name>`
  query param. When present, the summary (windows, cost, metrics) is
  computed over that provider's subscription-usable accounts only;
  unattributed request logs (`account_id IS NULL`) are excluded from a
  scoped summary. An unknown provider value returns a valid, empty summary
  (not an error). Omitting the param is unchanged (`totalUsd7d` etc. stay
  pool-global) — full back-compat.
- **Menubar scoping**: switching the §9.2 provider scope now re-fetches
  `/api/usage/summary` with `?provider=<scope>` and re-derives the metrics
  strip, the §11 value multiple (numerator AND denominator both scoped —
  this **supersedes** §11's "pool-global by construction" decision), and the
  status-bar icon percents from the scoped summary. A stale-fetch guard
  ensures a summary request started under a previously-selected scope can
  never overwrite state after the scope has since changed. Privacy-mode
  pseudonyms (§12) are unaffected — they stay keyed on the full account
  list regardless of scope, as already specified.
- **Two-ring status icon**: the menu-bar icon becomes two concentric
  monochrome rings — outer = 5-hour (primary) remaining %, inner = weekly
  (secondary, monthly fallback) remaining %. Unknown state draws track-only
  rings; risk state draws the outer ring plus the exclamation glyph and
  omits the inner ring for legibility at menu-bar size. Down/update
  treatments are unchanged. The icon follows the active provider scope like
  everything else.
- **Chip-free icon buttons**: icon-only chrome buttons (header eye/refresh/
  overflow, footer power) drop their circle glass chip background, matching
  every other flat SF Symbol in the design; hit targets stay 22×22 and
  accessibility labels are preserved. Text-carrying buttons (segmented
  scope control, etc.) keep their existing chips — this only affects
  icon-only affordances.

## Impact

- Server: `app/**` usage-summary endpoint/query path; no schema change, no
  new persisted state, additive optional query param only.
- Client-only otherwise (`clients/macos-menubar`) — no other API changes.
  Verified with `swift test` including new `ProviderScope`/`AppState`
  scoped-fetch cases, `StatusIconRenderer` two-ring cases, and a
  `CLAUDE_LB_DRY_RUN=1`/live round-trip against the scoped endpoint.
- Supersedes the §11 "pool-global by construction" decision recorded in
  `macos-menubar-app/design.md` at the owner's explicit direction; §11 is
  kept intact as a historical record and marked superseded by the new §13.
