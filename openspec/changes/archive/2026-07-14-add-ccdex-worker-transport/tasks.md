## 1. Contract

- [x] 1.1 Define the MCP tool surface, lifecycle, permission posture, isolation rules, durable state, and bounded result contract.
- [x] 1.2 Record the installed Claude Code CLI semantics and the read-only guarantee limitation.

## 2. Transport

- [x] 2.1 Add the dependency-free stdio JSON-RPC MCP server and input validation.
- [x] 2.2 Add detached supervision, atomic registry state, session capture, timeout, cancellation, watchdog, and orphan recovery.
- [x] 2.3 Add guarded write mode, transport-created worktrees, writer locks, bounded/redacted results, and safe diff summaries.

## 3. Verification

- [x] 3.1 Add deterministic unit tests for MCP negotiation, tool schemas/calls, validation, permission argv, redaction, bounds, and atomic metadata.
- [x] 3.2 Add deterministic integration tests for lifecycle, resume, timeout, cancellation, watchdog, preflight failures, registry recovery, and worktree locking.
- [x] 3.3 Run byte-compilation, Ruff, focused pytest, and strict OpenSpec validation sequentially.
