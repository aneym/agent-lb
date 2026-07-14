## Context

Normal Claude Code can plan and review with Fable, but its built-in Claude subagents cannot provide a direct GPT/Codex implementation lane. The existing `ccdex` launcher already supplies that lane by running the Claude Code harness through agent-lb's Codex compatibility route. This change exposes the same fail-closed path as a durable local MCP worker without adding another inference client or changing the server API.

The transport must survive MCP client restarts, keep prompts out of process arguments, preserve Claude Code session continuity, constrain write access, avoid concurrent writers in one checkout, and return useful bounded results without copying raw logs or credentials into the caller's context.

## Goals / Non-Goals

**Goals:**

- Provide start, status, result, reply, cancel, and list operations over dependency-free stdio JSON-RPC.
- Launch the installed `ccdex` path with durable lifecycle state and resumable Claude Code sessions.
- Make read-only the default and require an isolated worktree or explicit consent for workspace writes.
- Bound concurrency, time, logs surfaced to callers, and retry-loop behavior.
- Make process recovery and cancellation safe in the presence of stale or reused PIDs.

**Non-Goals:**

- Replace or bypass the existing Claude Code harness and agent-lb compatibility route.
- Select the normal Claude Code planner model or define machine-global routing policy.
- Provide an operating-system filesystem sandbox.
- Remove transport-created worktrees automatically or mutate unrelated git state.
- Make live or paid model calls in deterministic tests.

## Decisions

### Use a dependency-free executable stdio server

`clients/ccdex-worker-mcp` implements the MCP surface with the Python standard library. A standalone client executable matches the existing launcher distribution model and avoids coupling a local operator tool to the FastAPI service or a new package runtime. Embedding the worker API in the server was rejected because job ownership, filesystem access, and Claude Code process execution are machine-local concerns.

### Separate protocol handling from detached supervision

The MCP process validates calls and writes initial metadata, then starts a detached supervisor for each turn. The supervisor owns the worker process group, stream parsing, watchdog, timeout, and terminal-state publication. This keeps jobs alive across MCP disconnects and lets foreground waits share a bounded request pool without blocking independent status or cancellation calls. Keeping the worker as a child of the stdio server was rejected because client shutdown would orphan lifecycle state or terminate useful work.

### Persist one atomic registry directory per job

Each job has an atomically replaced `metadata.json`, per-turn stdout and stderr logs, and an optional worktree under `CCDEX_WORKER_HOME`. Writers serialize metadata changes with a lock; readers recover stale active jobs only after validating both PID and process-group identity. A database was rejected because the registry is local, append-light, operator-inspectable state and must work without service dependencies.

### Delegate model selection to the existing `ccdex` launcher

The transport invokes `ccdex`; it does not name a GPT model or call an inference API itself. This preserves the launcher's fail-closed compatibility probe and makes the launcher the enforcement point for the current Codex model. The deterministic `CCDEX_WORKER_BIN` override exists only for preflighted tests and operator-controlled substitution.

### Treat permissions as a harness boundary

Read-only jobs use Claude Code safe mode, `dontAsk`, and the exact `Read,Glob,Grep` tool allowlist. Workspace-write jobs require either a transport-created worktree or `allow_in_place=true`. This is reported as a Claude Code harness restriction, not an OS sandbox. Claiming stronger isolation was rejected because the worker process itself retains the operating-system access of its user.

### Use exclusive ownership tokens for write serialization

Workspace-write execution is protected by an exclusively created lock keyed by the real workspace path. The lock records job, turn, and a random owner token, and release removes only an exact owner match. Missing or malformed ownership remains contended. Advisory-only locking was rejected because concurrent MCP processes must agree without sharing memory.

### Return summaries, not raw streams

Results include bounded final text, lifecycle metadata, usage when present, a safely computed diff stat, and absolute log paths. Credential-shaped content is redacted from returned text and errors; raw stdout and stderr remain local artifacts. Returning raw streams was rejected because it would inflate orchestrator context and increase credential exposure.

## Risks / Trade-offs

- [Claude Code safe mode is not OS isolation] -> Label the limitation in every read-only job and require worktree or external containment when stronger boundaries are needed.
- [Detached jobs can outlive their caller] -> Persist inspectable metadata, expose cancel/list operations, enforce timeouts, and recover missing processes.
- [PID reuse could target an unrelated process] -> Require recorded PID and PGID identity matches before recovery or signalling.
- [Transport-created worktrees accumulate] -> Never delete automatically; return their paths so operators can inspect and clean them deliberately.
- [Redaction patterns cannot recognize every secret format] -> Keep raw logs out of MCP results, bound returned text, and treat on-disk logs as operator artifacts.
- [A real `ccdex` preflight can consume availability or wait] -> Use a bounded `--help` preflight and a deterministic worker override in tests.

## Migration Plan

1. Land the executable, deterministic tests, and OpenSpec artifacts together.
2. Install or link the executable next to the existing `ccdex` client.
3. Register it as a user-scoped MCP server for normal Claude Code.
4. Exercise a read-only start/result call, then an isolated workspace-write call, and confirm the recorded CCDEX model at the launcher/session boundary.
5. Roll back by removing the MCP registration and executable link; existing job directories remain inert and inspectable.

## Open Questions

- Should a future installer register the MCP server automatically, or keep registration an explicit machine-configuration step?
- Should transport-owned worktrees gain a separate opt-in cleanup command after retention and dirty-worktree semantics are specified?
