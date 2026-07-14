## Why

Normal Claude Code can delegate only through Claude-model subagents today, so sending a closed implementation task to GPT-5.6 Sol incurs an unnecessary Sonnet forwarding turn. The existing `ccdex` launcher already runs the real Claude Code harness through agent-lb's Codex bridge, but it is an interactive/headless command rather than a durable worker transport that normal `cc` can call directly.

## What Changes

- Add a dependency-free stdio MCP server at `clients/ccdex-worker-mcp` with start, status, result, reply, cancel, and list tools.
- Run each turn as a detached `ccdex` headless process with durable atomic metadata, bounded logs/results, session continuity, cancellation, timeout, retry-loop watchdog, and orphan recovery.
- Support read-only and guarded workspace-write jobs, optional transport-owned git worktree isolation, and one active writer per real workspace.
- Add focused deterministic tests that use a stub worker and make no live or paid model calls.

## Capabilities

### Modified Capabilities

- `claude-harness-codex`: expose the existing Claude Code + GPT compatibility path as a durable MCP worker transport for normal Claude Code.

## Impact

The change is additive and confined to one new client executable, focused tests, and this OpenSpec change. It does not alter server routes, the existing `ccdex` launcher contract, global Claude configuration, documentation, changelog, or running services.
