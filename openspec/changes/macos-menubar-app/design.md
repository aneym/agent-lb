# Agent LB — macOS 26 Menu Bar App
## Implementation Design Document

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
│  ● a.neyman17@gmail.com           62% ▮▮▮▮▮▮▯▯▯▯ │
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

## 3. Architecture

**12 Swift files.** App target `AgentLB`, test target `AgentLBTests`. No external dependencies.

```
clients/macos-menubar/
├── Makefile
├── Package.swift
├── Resources/
│   └── Info.plist
├── Sources/AgentLB/
│   ├── AgentLBApp.swift            (1)
│   ├── AppState.swift              (2)
│   ├── APIClient.swift             (3)
│   ├── APIModels.swift             (4)
│   ├── ServiceController.swift     (5)
│   ├── LaunchAtLogin.swift         (6)
│   ├── Format.swift                (7)
│   └── Views/
│       ├── RootView.swift          (8)
│       ├── PoolSection.swift       (9)
│       ├── AccountsSection.swift   (10)
│       ├── ActivitySection.swift   (11)
│       └── FooterBar.swift + Components (12)
├── Tests/AgentLBTests/
│   ├── ModelDecodingTests.swift
│   ├── ServiceControllerTests.swift
│   ├── FormatTests.swift
│   └── Fixtures/ (accounts.json, summary.json, projections.json,
│                  request-logs.json, version.json, health-ready.json)
└── scripts/verify-e2e.sh
```

### (1) `AgentLBApp.swift`
```swift
@main struct AgentLBApp: App
```
- Declares one `MenuBarExtra` scene, `.menuBarExtraStyle(.window)`.
- Label: `Image(nsImage: appState.statusIcon)` (template composite from `StatusIconRenderer`).
- Content: `RootView().environment(appState).frame(width: 340)`.
- Owns the single `@State private var appState = AppState()`; calls `appState.startBackgroundPolling()` in `init`.

### (2) `AppState.swift` — `@MainActor @Observable final class AppState`
The refresh engine + single source of truth. Public surface:
```swift
enum ServiceStatus { case running, degraded, starting, stopped, unreachable }

var serviceStatus: ServiceStatus
var summary: UsageSummary?
var projections: ProjectionsResponse?
var accounts: [Account]
var recent: [RequestLogEntry]
var version: RuntimeVersion?
var lastSyncAt: Date?
var sectionErrors: Set<Section>      // per-section soft failures
var statusIcon: NSImage              // recomputed on state change

func popoverOpened() / popoverClosed()    // wired to RootView onAppear/onDisappear
func refreshNow() async                   // ⟳ button
func pause(accountId: String) async
func reactivate(accountId: String) async
func startService() async / stopService() async / restartService() async
```
**Polling policy (RefreshEngine, embedded here):**
- **Closed:** one `Task` loop, every **30 s**: `GET /health` (sets running/stopped/unreachable) and every 4th tick (**120 s**) `GET /api/dashboard/projections` + `GET /api/runtime/version` → drives `statusIcon` only. Sub-second timeout budget; failures never alert.
- **Open** (`popoverOpened` starts a dedicated `Task`, cancelled on close — Pattern 3 `.task(id:)` from research): immediately fetch everything, then loop: `/api/usage/summary` + `/api/accounts` every **5 s**, `/api/request-logs?limit=5` every **10 s**, `/api/dashboard/projections` every **30 s**. All endpoint fetches inside one tick run concurrently via `async let`; each failure marks `sectionErrors` for its section only.
- Health classification: `/health` refused → check `ServiceController.isLoaded()`: PID present → `.unreachable`, else `.stopped`. `/health` ok + `/health/ready` 503 → `.degraded`.

### (3) `APIClient.swift` — `struct APIClient: Sendable`
```swift
enum APIError: Error {
  case serviceDown            // URLError .cannotConnectToHost / .networkConnectionLost
  case timeout                // URLError .timedOut
  case httpStatus(Int, body: String?)
  case decoding(DecodingError, endpoint: String)
  case transport(URLError)
}

let base = URL(string: "http://127.0.0.1:2455")!
// URLSession with ephemeral config: timeoutIntervalForRequest = 3,
// timeoutIntervalForResource = 5, waitsForConnectivity = false

func health() async throws                    // GET /health        (expects {"status":"ok"})
func ready() async throws -> Bool             // GET /health/ready  (true on 200, false on 503)
func startupComplete() async throws -> Bool   // GET /health/startup
func accounts() async throws -> AccountsResponse
func usageSummary() async throws -> UsageSummary
func projections() async throws -> ProjectionsResponse
func requestLogs(limit: Int) async throws -> RequestLogsResponse
func runtimeVersion() async throws -> RuntimeVersion
func pauseAccount(_ id: String) async throws       // POST /api/accounts/{id}/pause
func reactivateAccount(_ id: String) async throws  // POST /api/accounts/{id}/reactivate
```
- Generic `get<T: Decodable>(_ path: String)` / `post(_ path: String)`; no auth headers (auth mode disabled locally — §INPUTS).
- **Decoder:** one shared `JSONDecoder` with a custom date strategy, because the API mixes fractional (`2026-06-10T20:09:28.958334Z`) and whole-second (`2026-06-10T23:50:00Z`) ISO-8601:
```swift
decoder.dateDecodingStrategy = .custom { d in
  let s = try d.singleValueContainer().decode(String.self)
  return Format.iso8601Frac.date(from: s) ?? Format.iso8601.date(from: s)
    ?? { throw DecodingError... }()
}
```

### (4) `APIModels.swift` — Codable models
**Key fact: the API emits camelCase JSON** (`accountId`, `primaryRemainingPercent`, `totalUsd7d`) — field names map 1:1 to Swift property names. **No `keyDecodingStrategy`, no CodingKeys needed** except where noted. Decode only the fields the UI consumes; `Decodable` ignores extras. All `Decodable, Sendable, Equatable`; `Account`/`RequestLogEntry` also `Identifiable`.

```swift
struct AccountsResponse: Decodable { let accounts: [Account] }

struct Account: Decodable, Identifiable {
  let accountId: String                 // id { accountId }
  let provider: String
  let email: String?
  let alias: String?
  let displayName: String
  let workspaceLabel: String?
  let planType: String?
  let routingPolicy: String?
  let status: String                    // "active" | "paused" | "deactivated" | ...
  let usage: AccountUsage
  let resetAtPrimary: Date?
  let resetAtSecondary: Date?
  let resetAtMonthly: Date?
  let rateLimitResetAt: Date?
  let lastRefreshAt: Date?
  let deactivationReason: String?
  let isEmailDuplicate: Bool?
  var id: String { accountId }
}
struct AccountUsage: Decodable {
  let primaryRemainingPercent: Double?
  let secondaryRemainingPercent: Double?
  let monthlyRemainingPercent: Double?
}

struct UsageSummary: Decodable {
  let primaryWindow: UsageWindow?
  let secondaryWindow: UsageWindow?
  let monthlyWindow: UsageWindow?
  let cost: CostSummary?                // { currency, totalUsd7d }
  let metrics: SummaryMetrics?
}
struct UsageWindow: Decodable {
  let remainingPercent: Double?
  let capacityCredits: Double?
  let remainingCredits: Double?
  let resetAt: Date?
  let windowMinutes: Int?
}
struct CostSummary: Decodable { let currency: String?; let totalUsd7d: Double? }
struct SummaryMetrics: Decodable {
  let requests7d: Int?
  let errorRate7d: Double?
  let topError: String?
}

struct ProjectionsResponse: Decodable {
  let depletionPrimary: Depletion?
  let depletionSecondary: Depletion?
  let weeklyCreditPace: WeeklyCreditPace?
}
struct Depletion: Decodable {
  let risk: Double?
  let riskLevel: String?               // "safe"|"warning"|"danger"|"critical"
  let projectedExhaustionAt: Date?
  let secondsUntilExhaustion: Double?
}
struct WeeklyCreditPace: Decodable {
  let actualUsedPercent: Double?
  let scheduledUsedPercent: Double?
  let deltaPercent: Double?
  let status: String?                  // "behind"|"on_track"|"ahead"|"danger"
  let confidence: String?
}

struct RequestLogsResponse: Decodable {
  let requests: [RequestLogEntry]
  let total: Int
  let hasMore: Bool
}
struct RequestLogEntry: Decodable, Identifiable {
  let requestedAt: Date
  let requestId: String                // id
  let accountId: String?
  let model: String?
  let status: String                   // "ok" | "error"
  let errorCode: String?
  let tokens: Int?
  let costUsd: Double?
  let latencyMs: Double?
  var id: String { requestId }
}

struct RuntimeVersion: Decodable {
  let currentVersion: String?
  let latestVersion: String?
  let updateAvailable: Bool
  let releaseUrl: String?
}

struct HealthResponse: Decodable { let status: String }
```
Modeling notes for implementers: everything nullable in the sample stays `Optional` — render `—` for nils; `Int` is 64-bit on macOS so `total: 142968`-scale counters and token counts are fine; risk/pace strings are matched case-sensitively with a default branch (treat unknown as `safe`/`on_track` rendering, plain weight).

### (5) `ServiceController.swift` — `struct ServiceController: Sendable`
launchctl via `Process`; **all command construction pure & testable**, execution separated:
```swift
static let label = "com.aneyman.agent-lb"
static var plistPath: String {         // ~ expanded at runtime
  NSString("~/Library/LaunchAgents/com.aneyman.agent-lb.plist").expandingTildeInPath }
static var domainTarget: String { "gui/\(getuid())/\(label)" }   // never hardcode 501

// Pure builders (unit-tested):
static func statusCommand()  -> [String] // ["launchctl","list", label]
static func startCommand(loaded: Bool) -> [String]
   // loaded → ["launchctl","kickstart", domainTarget]
   // not loaded → ["launchctl","load", plistPath]
static func restartCommand() -> [String] // ["launchctl","kickstart","-k", domainTarget]
static func stopCommand()    -> [String] // ["launchctl","unload", plistPath]

// Execution:
func isLoadedWithPID() async -> Bool   // parse `launchctl list <label>` PID column ("-" = stopped)
func start() async throws / restart() async throws / stop() async throws
```
- Runs `/bin/launchctl` directly (`Process.executableURL = URL(filePath: "/bin/launchctl")`), captures stdout/stderr, throws `ServiceError.commandFailed(exit: Int32, stderr: String)` on non-zero exit.
- **Watchdog caveat (must be in code comment + Stop confirmation):** `stop()` unloads only the main plist; the watchdog `com.aneyman.agent-lb-watchdog` may relaunch the service. The Stop menu item shows a confirmation: “The watchdog may restart the service. Stop anyway?” — we deliberately never touch the watchdog plist.
- After `start()`: `AppState` polls `/health/startup` 1 s × 30 before declaring success.

### (6) `LaunchAtLogin.swift`
```swift
enum LaunchAtLogin {
  static var isEnabled: Bool { SMAppService.mainApp.status == .enabled }
  static func set(_ enabled: Bool) throws   // register()/unregister()
  static var requiresApproval: Bool { SMAppService.mainApp.status == .requiresApproval }
  static func openSettings() { SMAppService.openSystemSettingsLoginItems() }
}
```
Toggle in overflow menu reads status fresh each open; on `.requiresApproval`, show “Approve in System Settings…” item instead. No entitlement needed (`mainApp`, non-sandboxed). Note: launch-at-login only behaves predictably when run from the bundled `AgentLB.app`, not the bare `swift run` binary — document in Makefile help.

### (7) `Format.swift`
- `iso8601Frac` / `iso8601` (`ISO8601DateFormatter`, the former with `.withFractionalSeconds`).
- `relativeAge(Date) -> String` (“12s ago”, “2m ago”), `countdown(to: Date) -> String` (“0:15”, “3h 25m”), `usd(Double) -> String` (`$6,159.25`), `compact(Int) -> String` (`60.8k`), `percent(Double) -> String`, `latency(Double ms) -> String` (`12.2s` / `840ms`), `tokens(Int) -> String`.
- All pure, all unit-tested with fixed `Date` inputs.

### (8) `Views/RootView.swift`
- Root `ZStack`: glass backdrop (or `.regularMaterial` when `accessibilityReduceTransparency`) → `VStack(spacing: 0)`: `HeaderView` (private subview in this file) / state-switched body / `FooterBar`.
- Body switch on `appState.serviceStatus` + data presence → loading / service-down / content per §1.3.
- `.onAppear { appState.popoverOpened() }`, `.onDisappear { appState.popoverClosed() }`.
- `@Environment(\.dismiss)` for post-action dismissal (Dashboard, Quit).

### (9) `Views/PoolSection.swift` — `struct PoolSection: View`
Renders §1.2 Pool from `summary` + `projections`. Pure function of inputs (takes models as `let`s, no environment fetches) → trivially previewable.

### (10) `Views/AccountsSection.swift` — `AccountsSection`, `AccountRow`
List, row layout, context-menu actions calling `appState.pause/reactivate`, per-row in-flight state (`@State var pendingAction`). Sorting: active first (by `primaryRemainingPercent` desc), then rate-limited, paused, deactivated.

### (11) `Views/ActivitySection.swift` — `ActivitySection`, `ActivityRow`
Recent 5 requests + disclosure persistence.

### (12) `Views/FooterBar.swift` — `FooterBar`, plus shared `Components`
`FooterBar` (glass pill row in one `GlassEffectContainer`), `MonoMeter`, `StatusGlyph`, `CountdownText`, `SectionLabel`, and `StatusIconRenderer` (composes the four status-bar template `NSImage`s per §1.4, cached by state key).

---

## 4. Build System

**Decision: SwiftPM executable + manual bundle assembly via Makefile, ad-hoc signed.** Rationale: target is this machine only, macOS 26 only, no sandbox, no hardened runtime, no entitlements (SMAppService.mainApp needs none) — which is exactly the regime where the research says the manual approach is sound. It keeps the repo free of an Xcode project and xcodegen/swift-bundler toolchain dependencies, and `make` stays fully headless. (If notarized distribution is ever needed, revisit with xcodegen — noted in Makefile header comment.)

### 4.1 `Package.swift`
```swift
// swift-tools-version: 6.2
import PackageDescription
let package = Package(
  name: "AgentLB",
  platforms: [.macOS("26.0")],
  targets: [
    .executableTarget(name: "AgentLB", path: "Sources/AgentLB"),
    .testTarget(name: "AgentLBTests", dependencies: ["AgentLB"],
                path: "Tests/AgentLBTests",
                resources: [.copy("Fixtures")]),
  ]
)
```

### 4.2 `Resources/Info.plist`
```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleIdentifier</key><string>com.aneyman.agentlb.menubar</string>
  <key>CFBundleName</key><string>AgentLB</string>
  <key>CFBundleExecutable</key><string>AgentLB</string>  <!-- MUST match binary name exactly -->
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>CFBundleShortVersionString</key><string>1.0.0</string>
  <key>LSMinimumSystemVersion</key><string>26.0</string>
  <key>NSPrincipalClass</key><string>NSApplication</string> <!-- required; omit → no UI -->
  <key>NSHighResolutionCapable</key><true/>
  <key>LSUIElement</key><true/>                              <!-- no Dock icon, no launch flash -->
</dict></plist>
```

### 4.3 `Makefile`
```make
APP      := AgentLB.app
BIN      := .build/release/AgentLB
BUNDLE_ID:= com.aneyman.agentlb.menubar

.PHONY: build bundle run test clean

build:
	swift build -c release

bundle: build
	rm -rf $(APP)
	mkdir -p $(APP)/Contents/MacOS $(APP)/Contents/Resources
	cp $(BIN) $(APP)/Contents/MacOS/AgentLB
	cp Resources/Info.plist $(APP)/Contents/Info.plist
	codesign --force --deep --sign - $(APP)        # ad-hoc, same-machine only

run: bundle
	open $(APP)

test:
	swift test

clean:
	swift package clean
	rm -rf $(APP)
```
Targets map exactly to deliverable: `build` (compile), `bundle` (assemble `AgentLB.app`), `run`, `test`, `clean`. Known constraints baked in as comments: `CFBundleExecutable` is case-sensitive vs binary name; ad-hoc signature won't pass Gatekeeper elsewhere; adding any entitlement later invalidates this path.

---

## 5. Test Plan

### 5.1 Unit tests (`swift test`, no network, no launchctl execution)

**`ModelDecodingTests.swift`** — decode each fixture (verbatim captures of the real responses from the API map, stored under `Tests/AgentLBTests/Fixtures/`) with the production decoder:
- `accounts.json` → asserts `accountId == "2c436b54-a7e2-4299-9d6b-689ad2dda8cb"`, `usage.primaryRemainingPercent == 62.0`, `rateLimitResetAt` parses (whole-second ISO), `lastRefreshAt` parses (fractional ISO), nulls (`alias`, `workspaceId`) decode as nil, unknown keys (`additionalQuotas`, `requestUsage`) are ignored without error.
- `summary.json` → `cost.totalUsd7d == 6159.252347`, `metrics.requests7d == 60805`, `monthlyWindow == nil`.
- `projections.json` → `depletionPrimary.riskLevel == "critical"`, `weeklyCreditPace.status == "behind"`.
- `request-logs.json` → entry count, `costUsd == 0.009603`, `total == 142968` (Int64-range safe), error-shaped row with `errorCode` non-nil.
- `version.json` → `updateAvailable == false`, `latestVersion == nil`.
- Negative: malformed date string → throws `APIError.decoding`-mappable error.

**`ServiceControllerTests.swift`** — pure command builders:
- `statusCommand() == ["launchctl","list","com.aneyman.agent-lb"]`
- `startCommand(loaded: true)` uses `kickstart gui/<uid>/…` with the *runtime* uid (assert prefix `gui/` and suffix label, not literal 501); `startCommand(loaded: false)` uses `load` + expanded plist path.
- `restartCommand()` contains `-k`; `stopCommand()` is `unload` + plist path.
- PID-column parser: `"123\t0\tcom.aneyman.agent-lb"` → loaded; `"-\t0\t…"` → not running; empty/garbage → not running.

**`FormatTests.swift`** — countdown/relative-age/currency/compact-number with fixed dates (e.g. `countdown` to +15 min → `"0:15"`, +205 min → `"3h 25m"`; `usd(6159.252347) == "$6,159.25"`; `compact(60805) == "60.8k"`).

What is deliberately *not* unit-tested: SwiftUI view trees, glass rendering, MenuBarExtra lifecycle — covered by E2E.

### 5.2 End-to-end verification — `scripts/verify-e2e.sh`
```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

make test                                      # 1. unit tests green
make bundle                                    # 2. bundle assembles + signs
codesign --verify --deep AgentLB.app           # 3. signature valid
plutil -lint AgentLB.app/Contents/Info.plist   # 4. plist well-formed

curl -fsS --max-time 2 http://127.0.0.1:2455/health >/dev/null \
  || echo "WARN: agent-lb service down — app should show Stopped state"

open AgentLB.app                               # 5. launch
sleep 3
pgrep -x AgentLB >/dev/null                    # 6. process alive (LSUIElement: no Dock icon expected)

# 7. visual check: status item is in the menu bar (manual confirm via screenshot)
screencapture -x /tmp/agentlb-menubar.png
echo "Inspect /tmp/agentlb-menubar.png top-right for the AgentLB status icon."

# 8. teardown
pkill -x AgentLB || true
echo "E2E PASS"
```
Manual checklist appended to the script as comments (one-time per release): open popover → ready state renders real pool data; stop service (`launchctl unload …`) → reopen → Stopped state + Start button works and recovers; right-click an account → Pause → row flips to ⏸ and dashboard agrees; Reduce Transparency on → panel renders on opaque material with no layout breakage; Copy URL pastes `http://127.0.0.1:2455`.

---

## 6. Scope Guardrails (v1)

- **In:** one popover, status icon states, pool summary, accounts list with pause/reactivate, recent-5 activity, service start/stop/restart, Open Dashboard, Copy URL, launch-at-login, refresh.
- **Out (explicitly):** preference window, sparklines/charts, request-log filtering/pagination, quota-planner UI, reports, API-key views, notifications, Sparkle/updates (we only badge `updateAvailable` and link out), multi-instance/base-URL configuration (constant `127.0.0.1:2455`; if it ever needs to move, it becomes a single `@AppStorage("baseURL")` read in `APIClient.init` — leave the hook, build no UI).
- Endpoints used: `/health`, `/health/ready`, `/health/startup`, `/api/accounts`, `/api/usage/summary`, `/api/dashboard/projections`, `/api/request-logs`, `/api/runtime/version`, `/api/accounts/{id}/pause`, `/api/accounts/{id}/reactivate`. `/api/dashboard/overview` is intentionally unused in v1 (heavier; nothing in this layout needs it).
---

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
