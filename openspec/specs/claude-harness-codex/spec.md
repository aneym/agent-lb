# claude-harness-codex Specification

## Purpose

Define how agent-lb runs the real Claude Code harness with a locked GPT-5.6 Sol execution profile through a dedicated OpenAI Responses compatibility bridge.

## Requirements

### Requirement: Claude Code harness execution

The system SHALL provide a `ccdex` command that executes the installed Claude Code harness while routing inference through agent-lb's dedicated Codex compatibility path.

#### Scenario: Interactive launch

- **WHEN** an operator runs `ccdex`
- **THEN** the real Claude Code interactive harness starts with the compatibility route enabled

#### Scenario: Headless launch

- **WHEN** an operator runs `ccdex -p <prompt>`
- **THEN** Claude Code completes the headless turn through the compatibility route

### Requirement: Locked Sol execution profile

Every compatibility inference request MUST use canonical model `gpt-5.6-sol` and requested service tier `priority`, regardless of weaker Claude model, speed, or environment defaults. Reasoning effort SHALL honor a supported per-request value (`low`, `medium`, `high`, `xhigh`) supplied by the Claude Code harness via `output_config.effort` and MUST default to `high` when the value is missing or unsupported.

#### Scenario: Conflicting client controls

- **WHEN** Claude Code or its environment supplies a different model or service tier
- **THEN** the server sends `gpt-5.6-sol` and `priority` to the Responses route

#### Scenario: Per-task reasoning effort

- **WHEN** a compatibility request carries `output_config.effort` of `low`, `medium`, `high`, or `xhigh`
- **THEN** the translated Responses request and its accounting use that effort

#### Scenario: Unsupported effort value

- **WHEN** a compatibility request carries a missing or unsupported effort value
- **THEN** the translated Responses request uses `high`

### Requirement: Fail-closed launch

The `ccdex` launcher MUST exit nonzero when agent-lb or its compatibility capability is unavailable and MUST NOT fall back to plain Claude or an Anthropic account.

#### Scenario: Agent-lb unavailable

- **WHEN** preflight cannot reach a compatible agent-lb instance
- **THEN** `ccdex` reports the failure, exits nonzero, and does not execute an unproxied inference request

### Requirement: Messages and Responses protocol fidelity

The bridge MUST preserve ordered system and message text, supported images, tools, tool choice, parallel tool controls, tool calls, tool results, usage, stop reasons, streaming lifecycle, and Anthropic-native error envelopes across the Messages and Responses protocols.

#### Scenario: Tool-use round trip

- **WHEN** GPT-5.6 Sol streams a function call and Claude Code returns its tool result
- **THEN** Claude Code receives one valid `tool_use` block and the following Responses request contains the matching `function_call_output`

#### Scenario: Text streaming

- **WHEN** Responses emits streamed output text
- **THEN** the bridge emits an ordered Messages sequence ending with exactly one `message_delta` and one `message_stop`

### Requirement: Context overflow surfaces as a Claude Code compaction trigger

When an upstream Responses inference fails because the request exceeds the model context window (an upstream `context_length_exceeded` code, or a context-window/token-limit failure message), the ccgpt bridge MUST translate the failure into an Anthropic-native `invalid_request_error` whose message contains the phrase `prompt is too long`. It MUST NOT surface the overflow as a generic `api_error`, as a normal (empty) assistant success, or as any code the Claude Code harness classifies as retryable (for example `overloaded_error`).

The delivery channel is load-bearing: Claude Code only reactive-compacts when the failure is received as a non-200 HTTP response **before** it creates the assistant turn. Therefore a terminal overflow that occurs before any assistant content block MUST be surfaced as a non-200 HTTP error response (HTTP 400 for context overflow) carrying the Anthropic error envelope, and MUST NOT be surfaced as an HTTP 200 Server-Sent Events stream beginning with `message_start`. This holds whether the overflow is reported as the pre-stream non-streaming error response OR as an in-band terminal frame (a `response.failed` event or a top-level Codex `error` frame) that arrives before any content.

Only after visible assistant content (a `content_block_start` or `content_block_delta`) has already streamed does a subsequent overflow remain a genuine mid-stream failure: an in-band Anthropic `error` event of type `invalid_request_error` under HTTP 200, emitted with no trailing successful `message_delta` or `message_stop`. The startup peek used to make this distinction MUST be bounded—buffering only until the first content frame or the first terminal error frame—so it does not delay the first visible token or buffer the stream unboundedly. Non-overflow upstream errors MUST retain their existing translation.

#### Scenario: Pre-stream context overflow

- **GIVEN** a `/v1/ccgpt/messages` turn whose input exceeds the model context window
- **WHEN** the upstream returns a `context_length_exceeded` error before streaming begins
- **THEN** the endpoint returns an Anthropic error envelope of type `invalid_request_error` whose message contains `prompt is too long`
- **AND** the upstream HTTP status is preserved

#### Scenario: Pre-content in-band context overflow

- **GIVEN** a `/v1/ccgpt/messages` turn whose stream has emitted `response.created` but no assistant content
- **WHEN** the upstream stream then emits a `context_length_exceeded` `response.failed` failure
- **THEN** the endpoint returns an HTTP 400 Anthropic error envelope of type `invalid_request_error` whose message contains `prompt is too long`
- **AND** the response body contains no `message_start`

#### Scenario: Pre-content top-level Codex overflow frame

- **GIVEN** a `/v1/ccgpt/messages` turn whose stream has emitted `response.created` but no assistant content
- **WHEN** Codex emits a terminal `error` frame whose root `code` is `context_length_exceeded`
- **THEN** the endpoint returns an HTTP 400 Anthropic error envelope of type `invalid_request_error` whose message contains `prompt is too long`

#### Scenario: Mid-stream context overflow after content

- **GIVEN** a `/v1/ccgpt/messages` turn that has already streamed assistant content
- **WHEN** the upstream stream emits a `context_length_exceeded` failure
- **THEN** the bridge emits an in-band Anthropic `error` event of type `invalid_request_error` whose message contains `prompt is too long` under HTTP 200
- **AND** the bridge emits no trailing successful `message_delta` or `message_stop`

#### Scenario: Non-overflow upstream error is unchanged

- **GIVEN** a `/v1/ccgpt/messages` turn
- **WHEN** the upstream returns a non-overflow failure
- **THEN** the bridge surfaces it as an `api_error` carrying the upstream message

### Requirement: No hidden reasoning disclosure

The bridge MUST NOT expose private chain-of-thought and SHALL replay only validated opaque encrypted reasoning state needed for multi-turn continuity.

#### Scenario: Reasoning response

- **WHEN** Responses returns reasoning metadata or encrypted content
- **THEN** Claude Code receives no raw hidden reasoning text and only validated opaque state may be replayed

### Requirement: Safe token counting behavior

Compatibility token-count requests MUST NOT select or call an Anthropic account.

#### Scenario: Canonical token count unavailable

- **WHEN** Claude Code requests `/v1/messages/count_tokens` through `ccgpt` before a canonical GPT counter exists
- **THEN** agent-lb returns a conservative local estimate without upstream Anthropic traffic

### Requirement: Explicit Claude Code launch profiles

The launcher SHALL default normal Claude Code sessions to canonical Fable with high effort when the caller supplies no model or effort, and SHALL force CCDEX sessions to the canonical compatibility model with high effort regardless of caller model or effort arguments.

#### Scenario: Normal Claude Code default

- **WHEN** the normal launcher is invoked without a model or effort override
- **THEN** the executed Claude Code command names canonical Fable and high effort

#### Scenario: CCDEX ignores conflicting controls

- **WHEN** CCDEX is invoked with caller-supplied model or effort controls
- **THEN** the executed Claude Code command names only the canonical compatibility model and high effort

### Requirement: CCDEX child inference isolation

CCDEX MUST reject every Messages inference request whose body does not name the canonical compatibility model. It MUST NOT pass a Claude-model `Agent`, `Workflow`, resume, or other child request to the ordinary Anthropic route, and rejection MUST occur before selecting an Anthropic account or sending upstream inference.

#### Scenario: Claude-model agent request

- **WHEN** a CCDEX session emits a Messages request naming a Claude model
- **THEN** the launcher returns a deterministic non-success error
- **AND** no ordinary Anthropic request is sent

#### Scenario: Canonical GPT request

- **WHEN** a CCDEX session emits a Messages request naming the canonical compatibility model
- **THEN** the launcher rewrites it to the dedicated CCDEX compatibility route

#### Scenario: Token-count request

- **WHEN** a CCDEX session requests Messages token counting
- **THEN** the launcher uses the local compatibility counter without selecting an Anthropic account

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

Each job MUST have an atomically written durable registry entry under `CCDEX_WORKER_HOME`, defaulting to `~/.agent-lb/ccdex-jobs`, and MUST transition through `queued` and `running` to exactly one of `succeeded`, `failed`, `timeout`, `cancelled`, or `watchdog_killed`. Unchanged stream events MUST NOT cause durable metadata replacement. Process recovery and signalling MUST treat a worker as live only when its recorded PID and PGID match the operating system identity.

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

`ccdex_list` MUST return a bounded number of recent job summaries. A `background=false` start or reply MUST wait for terminal state while the MCP server remains able to process other JSON-RPC requests. The foreground response wait MUST end no later than the task timeout plus a bounded supervisor-finalization grace period, and request execution MUST use a bounded worker pool.

#### Scenario: Foreground start

- **WHEN** `ccdex_start` is called with `background=false`
- **THEN** that JSON-RPC response is delayed until terminal state while the server can continue serving independent requests

### Requirement: Opus frontend designer seat

The canonical Claude Code frontend-designer child SHALL use Opus for design direction and visual critique and MUST NOT inherit or explicitly select the Fable driver model.

#### Scenario: Frontend designer dispatch

- **WHEN** Claude Code dispatches the canonical `frontend-designer` agent without a per-invocation model override
- **THEN** the child resolves to the canonical Opus model
- **AND** the request does not consume Fable capacity

#### Scenario: No Fable fallback configuration

- **WHEN** the canonical frontend-designer definition is installed
- **THEN** its model field selects Opus
- **AND** the definition does not configure Fable as a fallback
