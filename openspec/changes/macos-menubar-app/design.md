# Agent LB — macOS 26 Menu Bar App
## Implementation Design Document

Related sections: [Product and visual design](design.md) · [Architecture](architecture.md) · [Build, verification, and scope](build-and-verification.md) · [Remote mode and refinements](remote-mode-and-refinements.md).

**Target:** macOS 26 only · Swift 6.2 · Xcode 26.0.1 toolchain (CLI only) · SwiftUI `MenuBarExtra` · Liquid Glass native, no fallbacks
**Location:** `clients/macos-menubar/`
**Backend:** `http://127.0.0.1:2455`, dashboard auth disabled (no credentials on any `/api/*` call)
**Service:** launchd label `com.aneyman.agent-lb` (watchdog: `com.aneyman.agent-lb-watchdog`)

---

## 1. Product Spec

### 1.1 Popover layout (ready state)

Panel: **340 pt wide × max 560 pt tall** (height hugs content; accounts list scrolls beyond 6 rows). `.menuBarExtraStyle(.window)`.

```
┌──────────────────────────────────────────────────┐
│  ● Agent LB              v1.20.0-beta.3   ⟳  ⋯   │  ← Header (glass chrome)
│    Running · synced 12s ago                      │     ⋯ = overflow menu
├──────────────────────────────────────────────────┤
│  POOL                                            │
│  ┌──────────────────────┬───────────────────────┐│
│  │ 5-HOUR               │ WEEKLY                ││
│  │ 95%                  │ 55%                   ││
│  │ ▮▮▮▮▮▮▮▮▮▮▮▮▮▮▮▯     │ ▮▮▮▮▮▮▮▮▯▯▯▯▯▯▯▯      ││
│  │ resets in 0:15       │ resets in 3h 25m      ││
│  │ risk: critical ⚠     │ pace: behind          ││
│  └──────────────────────┴───────────────────────┘│
│  $6,159.25 · 7d    60.8k req    err 0.68%        │
├──────────────────────────────────────────────────┤
│  ACCOUNTS (7)                                    │
│  ● a1-owner@example.com           62% ▮▮▮▮▮▮▯▯▯▯ │
│      claude · weekly 51% · resets 23:50          │
│  ◐ work@example.com  rate-limited 18:20          │
│      claude · weekly 12% · resets 20:59          │
│  ⏸ spare@example.com               paused        │
│      claude · weekly 88%                         │
│  ✕ old@example.com               re-auth needed  │
│   …(scrolls)                                     │
├──────────────────────────────────────────────────┤
│  RECENT                                          │
│  ✓ claude-sonnet-4-6   713 tok  $0.0096  12.2s   │
│  ✓ claude-sonnet-4-6   1.2k tok $0.0210   3.4s   │
│  ✕ claude-opus-4-6     rate_limit_exceeded       │
│  (5 rows max)                                    │
├──────────────────────────────────────────────────┤
│ ( Dashboard ) ( Copy URL ) ( Restart ) (⏻ menu)  │  ← Footer (glass pills)
└──────────────────────────────────────────────────┘
```

### 1.2 Sections, controls, behaviors

**Header** (fixed, glass chrome layer)
- Status dot + “Agent LB”: dot is **filled circle** (running+healthy), **half-filled** (degraded: `/health/ready` 503 or risk ≥ danger), **hollow circle with slash glyph** (service down). Monochrome — state expressed by shape/weight, never hue (matches dashboard identity).
- Sub-line: `Running · synced 12s ago` / `Starting…` / `Stopped` / `Unreachable`. Sync age comes from last successful `/api/usage/summary` fetch.
- Version label, right-aligned, secondary style. If `updateAvailable == true`: append `↑ update` in bold; clicking opens `releaseUrl`.
- `⟳` button: forces an immediate full refresh (all open-state fetches). Spins (system `ProgressView` swap) while in-flight.
- `⋯` overflow `Menu`: **Launch at Login** (toggle), **Stop Service**, **Start Service** (shown contextually), **Quit Agent LB**.

**Pool section** (content layer, source: `GET /api/usage/summary` + `GET /api/dashboard/projections`)
- Two columns: 5-hour (`primaryWindow`) and weekly (`secondaryWindow`).
- Each column: window title (11 pt caps, secondary), remaining percent (22 pt, SF Mono semibold, tabular), MonoMeter bar (see §2.4), reset countdown (`resetAt` → live `mm:ss` under 1 h, `Xh Ym` otherwise), and a status sub-line:
  - 5h column: `risk: {depletionPrimary.riskLevel}` — bold + `exclamationmark.triangle` glyph when `warning|danger|critical`; plain “risk: safe” otherwise.
  - weekly column: `pace: {weeklyCreditPace.status}` (`behind`/`on_track`/`ahead`/`danger`), bold when not `on_track`.
- Footer metrics row (single line, SF Mono 11 pt): `cost.totalUsd7d` formatted `$6,159.25 · 7d`, `metrics.requests7d` compact (`60.8k req`), `errorRate7d` as percent (`err 0.68%`). If `monthlyWindow != nil` and `secondaryWindow == nil`, the second column shows monthly instead, titled `MONTHLY`.

**Accounts section** (content layer, scrollable `ScrollView` + `LazyVStack`, source: `GET /api/accounts`)
- Header `ACCOUNTS (n)` — n = count.
- Row (two lines, 44 pt):
  - Line 1: StatusGlyph + `displayName` (13 pt, truncating middle) + right-aligned primary remaining `62%` (SF Mono) + 60 pt MonoMeter mini-bar of `usage.primaryRemainingPercent`.
  - Line 2 (11 pt secondary): `planType · weekly {secondaryRemainingPercent}% · resets {resetAtPrimary as local HH:mm}`. Overrides, in priority order:
    - `rateLimitResetAt` in the future → line 1 right side becomes bold `rate-limited HH:mm` (no bar), glyph ◐.
    - `status == "paused"` → right side `paused`, glyph ⏸ (`pause.circle`), row at 55% opacity.
    - `status == "deactivated"` or `deactivationReason != nil` → glyph ✕ (`xmark.circle`, bold), right side `re-auth needed` (if reason mentions auth) else `inactive`.
- StatusGlyph mapping (SF Symbols, all `.foregroundStyle(.primary/.secondary)`, no color): active → `circle.fill`; rate-limited/quota → `circle.lefthalf.filled`; paused → `pause.circle`; deactivated/reauth → `xmark.circle` (bold weight).
- Context menu (right-click / long-press) per row: **Pause** (`POST /api/accounts/{id}/pause`, shown when status active) or **Reactivate** (`POST /api/accounts/{id}/reactivate`, shown when paused/deactivated); **Copy Account ID**; **Open in Dashboard** (opens `{dashboard_url}#accounts`). Action rows show inline spinner until the POST resolves, then the section refreshes.
- `isEmailDuplicate == true` → append `·2` suffix to displayName disambiguation using `alias ?? workspaceLabel` when present.

**Recent section** (content layer, source: `GET /api/request-logs?limit=5`)
- Five rows max, single line each (11 pt): status glyph (`checkmark` plain / `xmark` bold), `model`, tokens compact, `costUsd` (`$0.0096`, SF Mono), latency (`12.2s` from `latencyMs`). Error rows replace tokens/cost with `errorCode` in bold.
- Whole section is a disclosure: collapsed by default if panel would exceed 560 pt; persisted in `@AppStorage("recentExpanded")`.

**Footer** (fixed, glass chrome layer — one `GlassEffectContainer`)
- **Dashboard** (`.glassProminent`): `NSWorkspace.shared.open(URL("http://127.0.0.1:2455/dashboard"))`, then `dismiss()`.
- **Copy URL** (`.glass`): copies `http://127.0.0.1:2455` to `NSPasteboard`; label flips to “Copied” for 1.2 s.
- **Restart** (`.glass`): `launchctl kickstart -k gui/<uid>/com.aneyman.agent-lb`; label flips to spinner until `/health` returns ok (timeout 20 s).
- **⏻** menu (glass): Start Service / Stop Service / Quit. (Duplicated from header overflow so power actions are one click away; both drive the same `ServiceController`.)
- All controls `.controlSize(.small)` per macOS HIG for dense extras.

### 1.3 States

| State | Trigger | Rendering |
|---|---|---|
| **Loading** | First open, no cached data yet | Header live; Pool/Accounts/Recent replaced by 3 redacted placeholder blocks (`.redacted(reason: .placeholder)`), no spinners; resolves in <1 s typically |
| **Service down** | `/health` connection refused / timeout | Header dot hollow+slash, “Stopped” (if `launchctl list` shows no PID) or “Unreachable” (PID exists but HTTP fails). Body replaced by centered empty-state: `bolt.slash` symbol (28 pt, secondary), “Agent LB service is not running”, and a **Start Service** `.glassProminent` button → `ServiceController.start()`. Footer: only Copy URL + ⏻ remain enabled. While starting: button → “Starting…” + spinner; poll `/health/startup` every 1 s, 30 s timeout, then error toast text under button |
| **Degraded** | `/health` ok but `/health/ready` 503 | Header dot half-filled, sub-line “Starting / draining”; body renders with last cached data at 70% opacity + thin banner “Service not ready” |
| **API error** | Health ok but a dashboard call fails (5xx/decode) | Affected section shows inline one-liner `couldn't load — retry` (tappable); other sections render normally. Never blank the whole panel for one failed endpoint |
| **Empty accounts** | `accounts == []` | Accounts section: `person.crop.circle.badge.questionmark` + “No accounts connected” + “Open Dashboard to add accounts” link |
| **Stale** | Last successful summary fetch > 120 s ago while open | Sub-line “synced 2m ago” turns bold; data remains |

### 1.4 Status-bar icon states

Base symbol: `point.3.connected.trianglepath.dotted` (reads as “load-balanced nodes”; template-rendered automatically). Icon-only, **no text** in the menu bar (HIG: text only for glanceable dynamic data; not warranted here). 18 × 18 pt.

| State | Icon | Driven by (closed-state poller) |
|---|---|---|
| Healthy | `point.3.connected.trianglepath.dotted` | `/health` 200 AND `depletionPrimary.riskLevel ∈ {safe, warning}` |
| Risk danger/critical | same symbol, `.symbolVariant(.fill)` + small `exclamationmark` badge composed via `Image(nsImage:)` template composite | `riskLevel ∈ {danger, critical}` |
| Service down | `point.3.connected.trianglepath.dotted` rendered at 40% alpha with `slash` overlay (composited template `NSImage`) | `/health` unreachable |
| Update available | base icon + 3 pt dot at top-right corner (template composite) | `/api/runtime/version.updateAvailable` |

Composites are drawn once into a template `NSImage` (`isTemplate = true`) in `StatusIconRenderer` (lives in `Components.swift`) — never colorized; the menu bar tints it.

---

## 2. Visual Design

The dashboard's identity is **ink-on-paper monochrome**: no hue, urgency via weight and shape, mono digits, compact type. The popover echoes that inside Apple's macOS 26 material system.

### 2.1 Where glass, where material, where plain

| Layer | Treatment | Rationale |
|---|---|---|
| Panel backdrop | Single `Rectangle().glassEffect(.regular, in: .rect(cornerRadius: 0)).ignoresSafeArea()` at the bottom of the root `ZStack` | macOS 26 does not auto-inject glass into a `.window` MenuBarExtra panel; this is the window-chrome layer, equivalent to system menus |
| Header + Footer | Controls sit **directly on the glass** (they are chrome). All glass buttons in the footer wrapped in **one `GlassEffectContainer`** (one compositing pass; avoids seam artifacts). Header `⟳`/`⋯` use `.buttonStyle(.glass)` inside the same container via a second `GlassEffectContainer` scoped to the header row | HIG: glass belongs to navigation/controls |
| Pool / Accounts / Recent content | Inset card: `.background(.thinMaterial, in: .rect(cornerRadius: 8))` — **never** `.glassEffect()` on rows or cards | HIG + pitfall #12: no glass on the content layer; material guarantees text contrast over glass backdrop |
| Section dividers | 1 px `Divider().opacity(0.5)` | matches dashboard hairline borders |

Hard rules: never nest `.glassEffect()` inside `.glassEffect()`; never combine a `.background()` material **and** `.glassEffect()` on the same view (double-glass artifact); respect `@Environment(\.accessibilityReduceTransparency)` — when true, swap panel backdrop to `.background(.regularMaterial)` (plain branch, no glass).

### 2.2 Typography (SF Pro / SF Mono — system fonts, echoing Geist/JetBrains roles)

| Role | Spec |
|---|---|
| Section labels (`POOL`, `ACCOUNTS`) | 11 pt, `.semibold`, `.secondary`, tracking +0.6, uppercase |
| Metric values (95%, $6,159.25) | `Font.system(size: 22, weight: .semibold, design: .monospaced)`, `.monospacedDigit()` |
| Row primary text | 13 pt `.regular`, `.primary` |
| Row secondary / sub-labels | 11 pt `.regular`, `.secondary` |
| Numeric inline data (tokens, cost, latency, %) | 11–13 pt `design: .monospaced` |
| Urgency | weight bump to `.bold` + glyph (`exclamationmark.triangle`, `xmark`) — **never color** |

### 2.3 Color

- Text: only `.primary` / `.secondary` / `.tertiary` semantic styles. Zero hardcoded colors, zero accent usage. This keeps the panel hue-free in both appearances, matching the OKLCH chroma-0 dashboard.
- Bars/dividers: `.quaternary` fill for track, `.primary` for fill (see MonoMeter).
- The only non-grayscale anywhere: none. Even error states are bold + glyph.

### 2.4 Components

- **MonoMeter** — the dashboard's quota bar: `Capsule().fill(.quaternary)` track (4 pt tall), overlay `Capsule().fill(.primary)` width = `remainingPercent/100`. When percent < 15: fill switches to `.primary` with a 2 px notch pattern? No — keep it pure: fill stays `.primary`, the *numeric label* goes bold. Shape stays constant.
- **StatusGlyph** — fixed 14 pt frame, symbols per §1.2.
- **CountdownText** — `TimelineView(.periodic(from:.now, by: 1))` for sub-hour countdowns; minute granularity above 1 h.

### 2.5 Spacing grid & animation

- 4 pt base grid. Panel padding 12 pt; section vertical gap 10 pt; intra-row gap 6 pt; card inner padding 10 pt.
- Animations: 150 ms `.easeOut` opacity fade on data refresh diffs (`.animation(.easeOut(duration: 0.15), value:)`); no entrance choreography, no springs, no `.interactive()` glass (no-op on macOS anyway). All honor Reduce Motion implicitly (opacity-only).

---
