## 1. Shared proxy runtime

- [x] 1.1 Add an explicit fixed-port parentless mode to `claude-lb-launch` without changing launcher-owned proxy lifetime behavior.
- [x] 1.2 Preserve inbound request identity, avoid a shared synthesized session ID, and keep model alias routing server-side.
- [x] 1.3 Preserve `CLAUDE_LB_DISABLE` and plain-Claude fallback behavior with proxy bypass exclusions.
- [x] 1.4 Add focused launcher unit tests and byte-compile validation.

## 2. Durable macOS installation

- [x] 2.1 Add a previewable dedicated LaunchAgent installer with bounded start and proxied-health verification.
- [x] 2.2 Add atomic preservation-safe settings checkpoint, conflict detection, idempotency, and conditional uninstall restore.
- [x] 2.3 Add deterministic installer lifecycle and settings-ownership tests.

## 3. Documentation and local verification

- [x] 3.1 Document install, logs, verification, restart, and uninstall in `README.md` and `GETTING-STARTED.md`, scoped to Claude Desktop Code.
- [x] 3.2 Correct stale Claude client documentation that still claims retired CCDEX artifacts are installed.
- [x] 3.3 Run ruff, targeted tests, strict OpenSpec validation, and a live Studio CLI/proxy smoke.
- [ ] 3.4 Run an actual Claude Desktop Code request and correlate it with fresh agent-lb evidence.

  Blocked on 2026-07-17: Claude Desktop 1.22209.0 did not expose CDP when
  relaunched with `--remote-debugging-port`, so the UI surface could not be
  driven safely by the available harness. The embedded runtime did emit fresh
  `/v1/code/sessions/*/worker/heartbeat` requests through the shared proxy,
  proving process-level routing, but those endpoints currently return 405 and
  are separate compatibility work.

## 4. Cross-machine rollout

- [x] 4.1 Commit and push the validated change on `main`.
- [x] 4.2 Fast-forward the MacBook checkout without disturbing unrelated work, install the LaunchAgent, and verify proxy health.
- [ ] 4.3 Run MacBook Claude Code and Claude Desktop Code end-to-end checks and record exact outcomes.

  Partial on 2026-07-17: the MacBook shared proxy passed the proxied Anthropic
  health check and fresh Claude Desktop Code heartbeat traffic reached its LB.
  A direct GPT request succeeded through the MacBook LB. The CLI Claude request
  reached the LB but could not complete because the local Claude OAuth session
  was expired and the available Anthropic pool then returned 429.
