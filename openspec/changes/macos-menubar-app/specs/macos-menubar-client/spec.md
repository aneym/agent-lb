# macOS Menu Bar Client

## ADDED Requirements

### Requirement: Menu bar app surfaces live pool and account state

The macOS menu bar client MUST render, from the local dashboard API at
`http://127.0.0.1:2455`, the pool usage windows (`/api/usage/summary`),
depletion risk and weekly pace (`/api/dashboard/projections`), the account
list with health/rate-limit/paused/deactivated state (`/api/accounts`), and
the five most recent requests (`/api/request-logs?limit=5`). A failure of any
single endpoint MUST degrade only its own section, never blank the panel.

#### Scenario: Service healthy with data

- **WHEN** the popover opens while `/health` returns ok
- **THEN** pool percentages, reset countdowns, account rows, and recent
  requests render from live API data within one refresh tick

#### Scenario: One endpoint fails

- **WHEN** `/api/request-logs` returns 500 while other endpoints succeed
- **THEN** only the Recent section shows an inline retry affordance and all
  other sections render normally

### Requirement: Service lifecycle control via launchd

The client MUST detect service-down (`/health` unreachable), distinguish
"stopped" (no launchd PID) from "unreachable" (PID present), and offer
start/restart/stop via `launchctl` against `com.aneyman.agent-lb` using the
runtime UID (never a hardcoded UID). Stop MUST warn that the watchdog
(`com.aneyman.agent-lb-watchdog`) may relaunch the service and MUST NOT
modify the watchdog itself. After start, the client MUST poll
`/health/startup` (1 s interval, bounded) before declaring the service up.

#### Scenario: Start from stopped state

- **WHEN** the service is stopped and the user clicks Start Service
- **THEN** the client runs the launchctl start command, polls
  `/health/startup` until ok (≤30 s), and transitions the UI to the running
  state

### Requirement: Decoding pinned to captured API fixtures

The client's Codable models MUST be exercised by unit tests that decode
verbatim captured responses (fixtures) of every consumed endpoint, so that
server-side response-shape regressions for consumed fields fail the client
test suite.

#### Scenario: Fixture decode

- **WHEN** `swift test` runs
- **THEN** every fixture in `Tests/AgentLBTests/Fixtures/` decodes through the
  production decoder with consumed fields asserted non-regressed

### Requirement: Configurable base URL with remote mode

The client MUST read its API base URL from the `baseURL` user default
(falling back to `http://127.0.0.1:2455`) so an install on another machine
can reach the service over Tailscale. When the base host is not local, the
client MUST hide all launchctl-backed service controls (start/stop/restart),
display the remote host in the header, and map unreachability to an
"unreachable" state with a retry affordance instead of offering a local
start.

#### Scenario: Remote install over Tailscale

- **WHEN** `baseURL` is set to `http://studio:2455` on a different machine
- **THEN** the popover renders live data from that host, shows `studio` in
  the header, and offers no Start/Stop/Restart controls

### Requirement: Native visual language

The client MUST follow macOS HIG for menu bar extras: a template-rendered
status icon (no fixed colors), Liquid Glass applied only to the chrome layer
(panel backdrop, header/footer controls), standard materials behind content,
and a monochrome presentation in which urgency is conveyed by weight and
glyph shape rather than hue. The client MUST honor Reduce Transparency by
substituting an opaque material backdrop.

#### Scenario: Reduce Transparency enabled

- **WHEN** macOS accessibility Reduce Transparency is on
- **THEN** the panel renders on an opaque material with identical layout and
  no glass effects

### Requirement: Headless build and bundle

`make bundle` inside `clients/macos-menubar/` MUST produce an ad-hoc-signed
`AgentLB.app` (LSUIElement, no Dock icon) from a SwiftPM release build with
no Xcode project, and `make test` MUST run the unit suite. The build MUST NOT
require network access or credentials.

#### Scenario: Clean machine build

- **WHEN** `make test && make bundle` runs on a macOS 26 machine with Xcode 26
  command line tools
- **THEN** tests pass and `AgentLB.app` exists with a valid ad-hoc signature
