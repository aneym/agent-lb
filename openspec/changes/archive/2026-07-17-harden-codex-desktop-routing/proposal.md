## Why

ChatGPT.app updates can rewrite Codex's active provider to direct `openai` even while the local Agent LB provider remains configured and healthy. That silently bypasses the local routing layer and can break desktop Codex requests until an operator notices and repairs `~/.codex/config.toml`.

## What Changes

- Add a repo-owned Codex routing guard that validates TOML, preserves unrelated configuration, and atomically repairs only the active provider and its loopback Agent LB provider fields.
- Add a previewable, reversible macOS installer for a dedicated LaunchAgent using `RunAtLoad`, `WatchPaths`, and a bounded interval fallback.
- Make the provider name host-configurable so existing `agent-lb` and `codex-lb` session/provider identities remain stable.
- Require local Agent LB health before a repair and record concise, secret-free repair/failure events without notifications.
- Add regression coverage for no-op operation, direct-provider repair, missing provider sections, malformed input, and idempotent atomic writes.

## Capabilities

### Modified Capabilities

- `deployment-installation`: Add durable macOS Codex desktop routing enforcement and lifecycle operations.

## Impact

Affected surfaces are a new standalone guard client, a macOS LaunchAgent installer, focused tests, and the deployment installation specification. Agent LB server routing and Codex session metadata are unchanged.
