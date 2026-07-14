# Context

## Purpose and scope

This transport lets normal Claude Code call GPT-5.6 Sol as a first-class worker without a Claude subagent forwarding turn. It launches the existing `clients/ccdex` command, so inference still uses the real Claude Code harness and agent-lb CCDEX bridge. It does not add another model API client.

## Process model

The MCP process owns protocol handling only and serves requests through a bounded thread pool, so a foreground wait does not block independent protocol requests. `ccdex_start` writes queued metadata and launches a detached supervisor process. The supervisor launches one headless `ccdex` process in a new process group, records PID/PGID, incrementally parses stream-JSON events, and atomically publishes terminal state. Foreground calls wait at most the task timeout plus a five-second finalization grace. This keeps jobs alive if the MCP client disconnects. Status/list calls recover stale registry entries when both supervisor and worker have disappeared or when a recorded PID no longer matches its PGID.

Each job directory contains `metadata.json`, per-turn stdout/stderr logs, and optionally a transport-created worktree. Metadata replacement uses write-to-temp, fsync, and `os.replace`; readers never observe partial JSON. Parsed events rewrite metadata only when session, final text, usage, turn, or lifecycle data actually changes.

## Claude Code CLI decisions

The installed Claude Code 2.1.207 help defines `[prompt]` as positional, `-p`/`--print` as a boolean, and `--input-format text` for print mode. The transport therefore invokes `--print --input-format text` without a positional prompt and feeds the prompt through a piped stdin while stdout/stderr continue streaming. Prompt text that begins like an option remains data. The CLI also supports `--output-format stream-json`, `--resume`, `--permission-mode`, `--tools`, `--safe-mode`, and session IDs in stream events. `plan` mode is a Claude Code permission policy, not an operating-system filesystem sandbox. The read-only worker therefore uses the strongest installed harness posture: safe mode plus an exact built-in tool allowlist (`Read,Glob,Grep`) and `dontAsk`. This prevents model-initiated writes through Claude Code tools and disables hooks/plugins/MCP customizations, but it is accurately reported as a harness boundary rather than an OS sandbox. Callers needing containment against the worker process itself should also use worktree isolation or an external sandbox.

Workspace-write jobs use `bypassPermissions` only after the transport verifies either a dedicated worktree or explicit `allow_in_place=true` consent. An exclusively created realpath lockfile identifies its exact job, turn, and random ownership token; a guarded reclaim/release path prevents concurrent acquisition and ABA deletion races. Missing or malformed owners stay contended for operator inspection. Worktree base refs beginning with `-` are rejected, and accepted refs are resolved with `git rev-parse --verify --end-of-options <ref>^{commit}` before only the resulting SHA reaches `git worktree add`. Worktree jobs get a unique detached worktree under the job directory and a lock on that worktree realpath. The transport never removes worktrees, so it cannot delete a user-created worktree accidentally.

## Failure handling

Timeout and cancellation verify that the worker PID still belongs to its recorded PGID before every group signal, send TERM, wait a bounded interval, then conditionally send KILL. Cancellation never targets the supervisor group. Repeated known retry-loop signatures are counted from stderr and non-JSON stdout transport errors; parsed assistant stream JSON cannot trigger the watchdog. The third eligible occurrence terminates the group as `watchdog_killed`. Missing binaries and failed `--help` preflight checks fail before launch. Terminal metadata retains absolute log paths for diagnosis while MCP results never inline raw logs.

## Result and privacy boundary

`ccdex_result` returns bounded final assistant text plus state, session ID, turn count, duration, exit code, usage when present, a safely computed git diff stat, and absolute log paths. Credential-shaped values are redacted from returned text and error fields. The on-disk raw logs are operator artifacts and are not sent over MCP.

## Example

A normal `cc` session calls `ccdex_start` with a coding prompt, `mode="workspace-write"`, and `isolation="worktree"`. The tool immediately returns a job ID. Later `ccdex_result` returns the worker's final summary and diff stat. A follow-up `ccdex_reply` launches another headless process with `--resume <captured-session-id>` in the same worktree, preserving the Claude Code harness conversation.
