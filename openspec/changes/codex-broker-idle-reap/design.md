## Context
Base plugin commit: db52e28f4d9ded852ab3942cea316258ae4ef346. No agent-lb install/update integration was found by repository-wide search for the plugin name or Claude plugin install/update commands.

## Goals / Non-Goals
Own a small reapplicable patch, preserve RPC and CLI, and reap only new broker-owned processes. Do not sweep old brokers, change session launch, or modify upstream git history.

## Decisions
Use the existing socket set and shutdown path. A broker-only client option creates a private POSIX process group, avoiding any signal to the broker's inherited group. Record the app-server descendant tree with repeated ps passes before signalling, since Codex puts MCP servers in their own groups; TERM every recorded group and member, wait on their existence rather than direct-child exit, then KILL survivors. Keep ordinary non-broker app-server clients and Windows behavior unchanged. Shutdown is single-flight and stops accepting clients immediately.

## Risks / Trade-offs
The tree walk covers descendants in any group, but not a process forked after the walk that leaves every recorded group. Tests must record actual group membership. Hard-killing the broker itself cannot run in-process cleanup. Updating files does not update already running brokers; this task deliberately leaves those alone.

## Migration Plan
Run scripts/apply-codex-plugin-cc-patch.sh after every plugin update/reinstall. No automatic integration exists. Reverse the patch only with an explicit checked git apply --reverse against the installed plugin.

## Open Questions
Live application and main merge may be blocked by sandbox write permissions. Full existing user MCP-fleet testing is excluded because it could read credentials or access external systems; test with isolated Codex configuration.
