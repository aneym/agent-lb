# Harden Launcher Shim Against Transient LB Restarts

## Why

The Claude launcher runs a local session shim that forwards each Claude Code
request to agent-lb. On any non-HTTP error it immediately returned `502` with no
retry. The live LB runs as a launchd service that restarts on deploys; during
the brief restart window the shim's connection is refused, so an in-flight
agent request fails even though the LB is back within a second or two. The repo
already relies on "the resilient launcher absorbs the brief restart," but the
shim itself had no reconnect logic — a restart mid-session could kill the
request the agent was making.

## What Changes

- The shim retries connection-level failures (connection refused/reset — the LB
  bouncing) with bounded exponential backoff before surfacing an error, so an
  in-flight request rides out a brief restart instead of failing the agent.
- Only "never reached the LB" failures are retried. Real HTTP responses
  (including `429`/`503` with `Retry-After`) pass straight through for the agent
  to handle, and read timeouts are not retried (the request may already be in
  flight, so re-sending could double-process it).
- Retry count and backoff are tunable and bounded
  (`CLAUDE_LB_SHIM_CONNECT_RETRIES`, `CLAUDE_LB_SHIM_CONNECT_BACKOFF`,
  `CLAUDE_LB_SHIM_CONNECT_BACKOFF_CAP`) so a sustained outage still surfaces an
  error rather than hanging indefinitely. Parent-process death stops retries.

## Impact

- Affected specs: `account-routing`.
- Affected code: `clients/claude-lb-launch` (session shim `_forward`,
  `_is_retryable_shim_connect_error`).
- No server changes. Behavior is additive: the only observable difference is that
  a brief LB connection failure is retried before the existing `502` is returned.
