## 1. Shared proxy runtime

- [x] 1.1 Add an explicit fixed-port parentless mode to `claude-lb-launch` without changing launcher-owned proxy lifetime behavior.
- [x] 1.2 Preserve inbound request identity, avoid a shared synthesized session ID, and keep model alias routing server-side.
- [x] 1.3 Preserve `CLAUDE_LB_DISABLE` and plain-Claude fallback behavior with proxy bypass exclusions.
- [x] 1.4 Add focused launcher unit tests and byte-compile validation.
- [x] 1.5 Route exact Messages paths to agent-lb and default all same-host auxiliary paths to direct, non-recursive Anthropic egress.
- [x] 1.6 Add regression coverage for path ownership, credential and identity preservation, HTTP passthrough, and direct failure behavior.

## 2. Durable macOS installation

- [x] 2.1 Add a previewable dedicated LaunchAgent installer with bounded start and proxied-health verification.
- [x] 2.2 Add atomic preservation-safe settings checkpoint, conflict detection, idempotency, and conditional uninstall restore.
- [x] 2.3 Add deterministic installer lifecycle and settings-ownership tests.

## 3. Documentation and local verification

- [x] 3.1 Document install, logs, verification, restart, and uninstall in `README.md` and `GETTING-STARTED.md`, scoped to Claude Desktop Code.
- [x] 3.2 Correct stale Claude client documentation that still claims retired CCDEX artifacts are installed.
- [x] 3.3 Run ruff, targeted tests, strict OpenSpec validation, and a live Studio CLI/proxy smoke.
- [x] 3.5 Prove a live auxiliary path bypasses agent-lb while a Messages path still reaches it.

  Verified 2026-07-17 with an isolated loopback proxy and fake LB: a proxied
  `POST /v1/messages` reached the fake LB and returned 200, while proxied
  `GET /api/oauth/account/settings` reached Anthropic directly, returned its
  native 401 authentication envelope, and produced no second fake-LB request.
- [x] 3.4 Run an actual Claude Desktop Code request and correlate it with fresh agent-lb evidence.

  Verified on 2026-07-17 with the Claude Code 2.1.209 executable bundled by
  Claude Desktop 1.22209.0. Through the installed proxy it returned exactly
  `STUDIO-CLAUDE-DESKTOP-RUNTIME-E0404D64-OK`, and the agent-lb log advanced
  from 667799 to 667876 lines. The reviewed proxy process started at
  17:50:38Z; all prior auxiliary-path 405s stopped at 17:50:26Z. The available
  GUI drivers lacked macOS Accessibility and Screen Recording grants, so the
  proof exercised Desktop's exact bundled runtime rather than automating its
  renderer.

## 4. Cross-machine rollout

- [x] 4.1 Commit and push the validated change on `main`.
- [x] 4.2 Fast-forward the MacBook checkout without disturbing unrelated work, install the LaunchAgent, and verify proxy health.
- [x] 4.3 Run MacBook Claude Code and Claude Desktop Code end-to-end checks and record exact outcomes.

  Verified on 2026-07-17 at commit `e0404d64`. The LaunchAgent was running on
  port 2458 and passed proxied health. An Anthropic OAuth request returned its
  native 401 while the matching LB-log count remained 0 to 0. A Messages
  request returned exactly `MACBOOK-DESKTOP-PROXY-E0404D64-OK`. The Claude
  Code 2.1.209 executable bundled by Desktop then returned exactly
  `MACBOOK-CLAUDE-DESKTOP-RUNTIME-E0404D64-OK` through the same proxy; a local
  LB token bypassed only the MacBook's expired Claude OAuth session.
