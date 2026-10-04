## Why

Codex rewrites `env_http_headers = { "x-agent-lb-seat" = "AGENT_LB_SEAT" }` as a `[model_providers.agent-lb.env_http_headers]` sub-table. The routing guard looked only at the provider table's own lines, missed the sub-table, and inserted the inline key again. The duplicate TOML key broke every `codex` start on Studio twice on 2026-10-04 ("failed to load configuration ... duplicate key").

## What Changes

- A managed key whose parsed value already matches, set through a sub-table or a dotted key, is left alone.
- A managed key set outside the provider table's lines to a different value fails with `GuardError` and no write.
- The guard inserts a managed key only when it is absent.

## Capabilities

### Modified Capabilities

- `deployment-installation`: the macOS Codex routing guard never writes a duplicate key for a value Codex stored in a sub-table.

## Impact

- `clients/codex-routing-guard` (`repaired_text`) and `tests/unit/test_codex_routing_guard.py`.
