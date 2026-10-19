Related sections: [Product and visual design](design.md) · [Architecture](architecture.md) · [Build, verification, and scope](build-and-verification.md) · [Remote mode and refinements](remote-mode-and-refinements.md).

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
