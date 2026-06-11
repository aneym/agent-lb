# Native macOS menu bar client for agent-lb

## Why

Everyday agent-lb interactions — "is the pool healthy?", "how close am I to the
5-hour limit?", "restart the service", "which account is rate-limited?" — today
require opening the browser dashboard. A native macOS menu bar app (inspired by
VibeProxy) puts that glanceable state and the most common quick actions one
click away in the status bar, with no browser round-trip.

## What Changes

- New client at `clients/macos-menubar/`: a SwiftUI `MenuBarExtra` app
  (`AgentLB.app`) targeting macOS 26, built headlessly with SwiftPM and a
  Makefile (manual `.app` bundle assembly, ad-hoc signed).
- The app consumes the existing local dashboard API (`http://127.0.0.1:2455`,
  dashboard auth disabled) read-mostly: `/health`, `/health/ready`,
  `/health/startup`, `/api/accounts`, `/api/usage/summary`,
  `/api/dashboard/projections`, `/api/request-logs`, `/api/runtime/version`,
  plus account `pause`/`reactivate` POSTs.
- Service control via `launchctl` against `com.aneyman.agent-lb` (start,
  restart, stop with watchdog caveat); launch-at-login via `SMAppService`.
- Visual design echoes the monochrome dashboard identity inside the macOS 26
  Liquid Glass material system: glass on chrome (header/footer controls),
  material on content, urgency by weight/shape, never hue.
- No server-side changes: zero new endpoints, zero schema changes.

## Impact

- Affected specs: new capability `macos-menubar-client`.
- Affected code: `clients/macos-menubar/**` (new; Swift, no Python changes).
- The existing dashboard API becomes a consumed-by-native-client surface;
  response shapes used by the app are pinned by decoding tests against
  captured fixtures, so breaking those fields now has a client-side canary.
