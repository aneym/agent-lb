## Context

The existing `cc` launcher creates a short-lived local HTTPS interception proxy for each Claude Code process. Claude Desktop's embedded Code runtime does not enter through that launcher, so a manual experiment placed a temporary proxy URL in `~/.claude/settings.json`. Stopping that temporary process disconnected every Claude process reading the setting. The durable design must preserve the current per-session path, keep interception loopback-only, and make the settings cutover conditional on a verified proxy.

## Goals / Non-Goals

**Goals:**

- Reuse the existing narrowly scoped TLS interception implementation.
- Run one fixed-port shared proxy under launchd with health-gated, atomic configuration.
- Preserve unrelated settings and provide conditional rollback.
- Keep server-side alias routing and request identity semantics authoritative.
- Prove the embedded Claude Desktop Code flow on Studio and MacBook.

**Non-Goals:**

- Routing ordinary Claude Desktop chat; current evidence supports only its embedded Code runtime.
- Installing the local CA in the system trust store.
- Changing the agent-lb server API, model alias table, or account-selection algorithm.
- Replacing the launcher-owned proxy used by `cc`.

## Decisions

1. **One proxy implementation, explicit lifetime mode.** `run_lb_proxy` accepts an explicit fixed port and shared lifetime flag. Launcher mode retains its parent watchdog; shared mode omits it. This avoids duplicating sensitive proxy code while keeping lifecycle semantics visible.

2. **LaunchAgent plus start-before-cutover installer.** A dedicated `com.aneyman.agent-lb-claude-desktop-proxy` LaunchAgent owns the shared process. The installer verifies port ownership and a proxied Anthropic health request before atomically updating settings. A simple background process is rejected because it cannot survive login, crash, or reboot.

3. **Server owns model compatibility.** The shared proxy forwards canonical paths and bodies. The existing server alias registry decides when GPT compatibility translation applies; the client does not rewrite every `gpt-*` request to one hard-coded bridge.

4. **Conditional settings ownership.** Installation records prior values for exactly `HTTPS_PROXY`, `https_proxy`, and `NODE_EXTRA_CA_CERTS`. Conflicts abort instead of being overwritten. Uninstall restores a prior value only if the current value is still installer-owned, preventing rollback from destroying later operator edits.

5. **Bypass through no-proxy exclusions.** Because Claude settings supply the global proxy URL to CLI subprocesses too, documented launcher bypass/fallback paths add `api.anthropic.com` to `NO_PROXY` and `no_proxy`. This preserves direct first-party behavior without weakening other proxy settings.

6. **Exact LB path ownership, default-direct auxiliary traffic.** CONNECT exposes only the hostname, so the proxy must terminate TLS for all `api.anthropic.com` requests before it can inspect their path. After decryption, only `/v1/messages`, `/v1/messages/count_tokens`, and the installer's `/health` probe route to agent-lb. Every other path goes to Anthropic over a fresh direct TLS connection that does not consult `HTTPS_PROXY`. A default-direct rule is safer than a denylist because new Claude OAuth, telemetry, feature, and worker APIs remain first-party automatically. HTTP responses never trigger cross-target failover: replaying a non-idempotent request after an LB or Anthropic response could duplicate work or disclose it to the wrong service.

## Risks / Trade-offs

- **Claude Desktop configuration behavior can change between app releases.** → Document the embedded Code scope and require an actual UI Code request plus fresh LB evidence on each target machine.
- **A fixed port can collide with another process.** → Refuse foreign listeners and never kill them; allow an operator-configurable port.
- **A global Claude setting affects CLI processes.** → Preserve launcher bypass with no-proxy exclusions and make uninstall explicit and conditional.
- **The CA grants the local proxy authority for `api.anthropic.com` inside opted-in Claude processes.** → Keep the private key mode `0600`, never add it to the system trust store, and intercept only the existing allowlisted host.
- **A future Anthropic API may need pooled routing.** → Default it direct until agent-lb implements and explicitly allowlists that contract; do not infer ownership from a broad `/v1` prefix.

## Migration Plan

1. Validate launcher and installer tests without changing the live shared process.
2. Install the durable LaunchAgent and verify the proxy before the settings cutover.
3. Relaunch Claude Desktop, submit an embedded Code request, and correlate it with a fresh agent-lb request/session record.
4. Push `main`, fast-forward the MacBook, run the same installer, and repeat verification.
5. Roll back with `scripts/install-claude-desktop-proxy.sh --uninstall`; it unloads the dedicated job and conditionally restores installer-owned settings.

## Open Questions

- Whether a future Claude Desktop release provides a dedicated per-app proxy configuration that can replace the shared Claude settings entry.
