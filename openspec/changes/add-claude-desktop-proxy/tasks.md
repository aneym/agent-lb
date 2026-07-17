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

## 4. Cross-machine rollout

- [ ] 4.1 Commit and push the validated change on `main`.
- [ ] 4.2 Fast-forward the MacBook checkout without disturbing unrelated work, install the LaunchAgent, and verify proxy health.
- [ ] 4.3 Run MacBook Claude Code and Claude Desktop Code end-to-end checks and record exact outcomes.
