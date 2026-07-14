## ADDED Requirements

### Requirement: Direct CCDEX worker MCP transport

The system SHALL provide a dependency-free stdio MCP server that exposes `ccdex_start`, `ccdex_status`, `ccdex_result`, `ccdex_reply`, `ccdex_cancel`, and `ccdex_list` and launches the existing `ccdex` Claude Code harness path rather than forwarding through a Claude subagent.

#### Scenario: MCP negotiation and discovery

- **WHEN** a client sends `initialize` followed by `tools/list`
- **THEN** the server returns its supported protocol version and schemas for all six tools, independent of the client's requested version

#### Scenario: MCP notification

- **WHEN** a client sends a JSON-RPC notification without an ID
- **THEN** the server emits no response

#### Scenario: Headless worker launch

- **WHEN** `ccdex_start` receives a valid prompt and working directory
- **THEN** it preflights the configured worker binary and launches a detached Claude Code stream-JSON turn with `CLAUDE_LB_SESSION_ID` set to the job ID
- **AND** the prompt is supplied as text on stdin and is not present in the worker argv

### Requirement: Durable lifecycle and recovery

Each job MUST have an atomically written durable registry entry under `CCDEX_WORKER_HOME`, defaulting to `~/.agent-lb/ccdex-jobs`, and MUST transition through `queued` and `running` to exactly one of `succeeded`, `failed`, `timeout`, `cancelled`, or `watchdog_killed`.
Unchanged stream events MUST NOT cause durable metadata replacement. Process recovery and signalling MUST treat a worker as live only when its recorded PID and PGID match the operating system identity.

#### Scenario: MCP process exits

- **WHEN** the MCP stdio process exits after starting a background job
- **THEN** the detached supervisor continues the job and a later MCP process can read its status and result

#### Scenario: Orphaned active metadata

- **WHEN** status inspection finds a queued or running job whose recorded supervisor and worker processes are both absent
- **THEN** the registry atomically marks the job failed with an orphan-recovery error

#### Scenario: Reused worker PID

- **WHEN** an active job's worker PID exists but belongs to a different PGID than recorded
- **THEN** recovery marks the job failed and cancellation does not signal that process group or the supervisor group

### Requirement: Guarded permissions

Read-only jobs MUST invoke the installed Claude Code harness with safe mode, `dontAsk`, and an exact `Read,Glob,Grep` built-in tool allowlist. The transport MUST describe this as a Claude Code harness restriction, not an operating-system filesystem sandbox. Workspace-write jobs MUST fail unless `isolation=worktree` or `allow_in_place=true`.

#### Scenario: Default read-only job

- **WHEN** `ccdex_start` omits `mode`
- **THEN** the worker argv contains the read-only harness posture and metadata reports its non-OS-sandbox limitation

#### Scenario: Unguarded write request

- **WHEN** a caller requests `mode=workspace-write`, `isolation=none`, and does not set `allow_in_place=true`
- **THEN** the tool rejects the request before launching a process

### Requirement: Worktree isolation and writer exclusion

Worktree isolation MUST reject non-git working directories, MUST resolve a non-option `base_ref` to a commit using end-of-options protection, MUST pass only the resolved commit SHA to worktree creation, MUST create a unique transport-owned git worktree under the job directory, and MUST NOT remove any worktree. No two active workspace-write jobs may hold the same execution realpath lock. Writer ownership MUST be established by exclusive lockfile creation and include the job ID, turn, and an unguessable token; release MUST remove only the exact owner, while missing or malformed ownership MUST remain contended.

#### Scenario: Duplicate in-place writer

- **GIVEN** an active workspace-write job holds the realpath lock for a working directory
- **WHEN** another workspace-write job requests the same directory in place
- **THEN** the second request is rejected

#### Scenario: Hostile base ref

- **WHEN** a worktree request supplies a `base_ref` beginning with `-`
- **THEN** the request is rejected before git worktree creation

### Requirement: Session continuity

The supervisor MUST capture the Claude session ID from stream-JSON initialization/system events, and `ccdex_reply` MUST resume that session in the same execution workspace.

#### Scenario: Follow-up turn

- **GIVEN** a completed job with a captured Claude session ID
- **WHEN** `ccdex_reply` receives another prompt
- **THEN** its worker argv contains `--resume <session-id>` and the resulting metadata remains associated with the original job

### Requirement: Bounded supervision

The supervisor MUST terminate the worker process group with TERM followed by bounded KILL escalation on cancellation, timeout, or the third occurrence of a known retry-loop signature. Retry signatures MUST be counted only from stderr or non-JSON stdout transport errors, never from parsed stream-JSON assistant content.

#### Scenario: Timeout

- **WHEN** a worker exceeds `timeout_s`
- **THEN** its process group is terminated and the job becomes `timeout`

#### Scenario: Retry loop

- **WHEN** worker output contains a known retry-loop signature at least three times
- **THEN** its process group is terminated and the job becomes `watchdog_killed`

### Requirement: Bounded redacted result

`ccdex_result` MUST return bounded final assistant text and structured metadata including state, session ID, turns, duration, exit code, usage when present, safe diff stat when available, and absolute stdout/stderr paths. It MUST NOT inline raw stdout or stderr and MUST redact credential-shaped material from returned text and errors.

#### Scenario: Oversized credential-bearing result

- **WHEN** the final worker event contains text beyond the result limit and credential-shaped values
- **THEN** the returned text is truncated, credentials are redacted, and raw log contents are absent

### Requirement: Fail-closed preflight and validation

The transport MUST reject missing or non-executable worker binaries, failed preflight checks, malformed tool inputs, unknown tools, and unknown job IDs without starting paid inference.

#### Scenario: Deterministic worker override

- **WHEN** `CCDEX_WORKER_BIN` names a test stub
- **THEN** preflight and worker execution use that exact executable without invoking the live `ccdex` binary

### Requirement: Bounded listing and synchronous waits

`ccdex_list` MUST return a bounded number of recent job summaries. A `background=false` start or reply MUST wait for terminal state while the MCP server remains able to process other JSON-RPC requests.
The foreground response wait MUST end no later than the task timeout plus a bounded supervisor-finalization grace period, and request execution MUST use a bounded worker pool.

#### Scenario: Foreground start

- **WHEN** `ccdex_start` is called with `background=false`
- **THEN** that JSON-RPC response is delayed until terminal state while the server can continue serving independent requests
