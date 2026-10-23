## Why
Claude Code server tools lack client input schemas and are rejected before reaching Anthropic.

## What Changes
Accept versioned server tools on Messages and explicitly reject them on the Codex bridge.

## Capabilities
### New Capabilities
- `anthropic-messages-compat`: server-tool pass-through and bridge rejection.

## Impact
Messages request validation and Codex bridge errors change. Custom tool schemas remain required.
