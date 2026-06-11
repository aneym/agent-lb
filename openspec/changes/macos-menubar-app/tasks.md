# Tasks

## 1. Foundation

- [x] 1.1 Scaffold `clients/macos-menubar/` (Package.swift, Info.plist, Makefile, directory layout per design.md §3-4).
- [x] 1.2 Capture live API fixtures under `Tests/AgentLBTests/Fixtures/` (done during design: accounts, summary, projections, request-logs, version, health, health-ready).
- [x] 1.3 Codable models (`APIModels.swift`) matching the real camelCase JSON; mixed-precision ISO-8601 date decoding.
- [x] 1.4 `APIClient` (async/await, 3 s request timeout, error taxonomy), `ServiceController` (pure launchctl command builders + Process execution), `LaunchAtLogin` (SMAppService), `Format` helpers.

## 2. UI

- [x] 2.1 `AgentLBApp` MenuBarExtra (.window style) + `AppState` (@Observable, open/closed polling engine, status icon states).
- [x] 2.2 `RootView` with header/footer glass chrome, state switch (loading / down / degraded / ready), reduce-transparency fallback.
- [x] 2.3 Pool section (5-hour + weekly windows, risk/pace, 7d cost/requests/error metrics).
- [x] 2.4 Accounts section (status glyphs, MonoMeter bars, rate-limit/paused/deactivated overrides, pause/reactivate context menu).
- [x] 2.5 Recent activity section (last 5 requests) + footer quick actions (Dashboard, Copy URL, Restart, power menu).

## 3. Verification

- [x] 3.1 Unit tests: fixture decoding, launchctl command builders, format helpers (`swift test` green).
- [x] 3.2 `make bundle` produces signed `AgentLB.app`; `scripts/verify-e2e.sh` passes.
- [x] 3.3 (verified 2026-06-11: popover screenshot on studio matches design — 460×701 panel exactly equals PanelLayout computed height; ring icon in menu bar; MacBook remote install verified by user) Live E2E on this machine: app launches, status item renders, popover shows real pool/account data from the running service; screenshot evidence.
- [x] 3.4 Design/code review pass (HIG + Liquid Glass usage, correctness, concurrency).
