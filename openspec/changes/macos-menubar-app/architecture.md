Related sections: [Product and visual design](design.md) · [Architecture](architecture.md) · [Build, verification, and scope](build-and-verification.md) · [Remote mode and refinements](remote-mode-and-refinements.md).

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
