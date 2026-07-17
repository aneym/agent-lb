## Context

Codex reads `~/.codex/config.toml`, which ChatGPT.app legitimately edits during upgrades. Making the file immutable would block supported app behavior, while replacing the whole document would risk user settings and credentials. Studio currently uses provider identity `agent-lb`; MacBook uses `codex-lb`. Both point at the same local endpoint.

## Goals / Non-Goals

**Goals:**

- Repair only the routing invariants after app writes.
- Keep each host's configured provider identity stable.
- Fail closed on malformed TOML and local Agent LB unavailability.
- Make writes atomic, mode-preserving, idempotent, and race-aware.
- Install a low-maintenance per-user launchd watcher with a periodic fallback.

**Non-Goals:**

- Preventing ChatGPT.app from editing Codex configuration.
- Copying whole config files between hosts.
- Retagging existing Codex sessions.
- Sending desktop notifications.

## Decisions

1. **Text-preserving repair after full TOML validation.** The guard parses the complete document first, then edits only top-level `model_provider` and the selected provider table's four routing keys. Unknown keys, comments, ordering, and every unrelated byte remain unchanged.
2. **Per-host provider identity.** The installer accepts an explicit provider and otherwise selects the active correctly routed provider, then a known correctly routed provider, before falling back to `agent-lb`. This avoids unnecessary resume/session filtering changes.
3. **Health-gated atomic replace.** A repair proceeds only after loopback `/health` succeeds. The guard writes a same-directory temporary file, preserves mode, fsyncs it, checks that the source did not change during preparation, atomically replaces it, and fsyncs the directory.
4. **Event-driven plus fallback launchd execution.** A dedicated LaunchAgent uses `RunAtLoad`, `WatchPaths` for the config, and a five-minute `StartInterval`. It is not kept alive and sends no notifications. The script writes only repair/failure events to a private local log and deduplicates recurring failures for one hour.
5. **Explicit lifecycle.** Preview does not mutate. Install owns only a recognizable dedicated plist and refuses to replace foreign content. Uninstall unloads and removes that plist but intentionally leaves the valid Codex routing configuration in place.

## Risks / Trade-offs

- launchd `WatchPaths` can coalesce rapid writes. The interval fallback and race-aware retry ensure eventual convergence.
- Agent LB may be unavailable during login. Health-gated repair avoids switching a client to a dead endpoint; the interval retries later.
- A future ChatGPT release may change provider schema. Full TOML validation and exact-key ownership turn unknown structure into a safe failure rather than config loss.

## Example

If an update changes only `model_provider = "agent-lb"` to `model_provider = "openai"`, the watcher restores `agent-lb` once `http://127.0.0.1:2455/health` is healthy. Existing profiles, approvals, MCP servers, comments, and direct-provider tables remain byte-for-byte unchanged.
