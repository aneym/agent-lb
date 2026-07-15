# claude-harness-codex delta

## REMOVED Requirements

### Requirement: Claude Code harness execution

**Reason**: The `ccdex` entry point is retired (2026-07-15). Claude Code runs
as the single raw harness; non-Claude worker lanes will return through the
agent-lb model-alias registry instead of a dedicated launcher mode.

### Requirement: Fail-closed launch

**Reason**: Retired with the `ccdex` launcher.

### Requirement: CCDEX child inference isolation

**Reason**: Retired with the `ccdex` launcher mode. The launcher's codex-mode
internals remain dormant in `clients/claude-lb-launch` until the alias-registry
change replaces or removes them; no supported entry point reaches them.

### Requirement: Direct CCDEX worker MCP transport

**Reason**: The `ccdex-worker` MCP transport is retired; its executable, tests,
and user-scope registration are removed.

### Requirement: Durable lifecycle and recovery

**Reason**: Worker-transport requirement retired with the transport.

### Requirement: Guarded permissions

**Reason**: Worker-transport requirement retired with the transport.

### Requirement: Worktree isolation and writer exclusion

**Reason**: Worker-transport requirement retired with the transport.

### Requirement: Session continuity

**Reason**: Worker-transport requirement retired with the transport.

### Requirement: Bounded supervision

**Reason**: Worker-transport requirement retired with the transport.

### Requirement: Bounded redacted result

**Reason**: Worker-transport requirement retired with the transport.

### Requirement: Fail-closed preflight and validation

**Reason**: Worker-transport requirement retired with the transport.

### Requirement: Bounded listing and synchronous waits

**Reason**: Worker-transport requirement retired with the transport.

## MODIFIED Requirements

### Requirement: Explicit Claude Code launch profiles

The launcher SHALL default normal Claude Code sessions to canonical Fable with
high effort when the caller supplies no model or effort.

#### Scenario: Normal Claude Code default

- **WHEN** the normal launcher is invoked without a model or effort override
- **THEN** the executed Claude Code command names canonical Fable and high effort
