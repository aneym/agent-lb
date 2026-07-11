# Harden agent connection resilience across LB restarts and long turns

## Why

Agents kept getting cut off on 2026-07-11 through two distinct connection
failures:

1. `claude-lb-launch` shim sessions surfaced `502 <urlopen error [Errno 32]
   Broken pipe>` when agent-lb restarted or was down: the shim's connect retry
   budget (6 attempts, ~15s) was far shorter than a watchdog-driven recovery
   (~65s), so any outage longer than 15s killed the agent's turn.
2. Client-facing websocket sessions died every few minutes with
   `1011 keepalive ping timeout`: uvicorn's default 20s pong deadline cuts
   agent clients that block their event loop during long local work. The
   upstream websocket leg already disables this pong watchdog by design
   (`proxy_websocket.py`), but the server leg did not match.

## What Changes

- Raise the shim's default connect retry budget from 6 attempts (~15s) to 32
  attempts (~120s) so sessions ride out a full watchdog recovery. The
  `CLAUDE_LB_SHIM_CONNECT_RETRIES` / `..._BACKOFF` / `..._BACKOFF_CAP` env
  overrides are unchanged; defaults move to module constants.
- Run uvicorn with `ws_ping_interval=20.0, ws_ping_timeout=None`: transport
  pings continue (intermediary liveness) but the pong deadline is disabled,
  matching the documented upstream-leg policy of letting agent-lb's own
  request/idle budgets decide when a turn has stalled.

## Impact

- Affected specs: `runtime-portability`, `proxy-admission-control`
- Affected code: `clients/claude-lb-launch`, `app/cli.py`,
  `tests/unit/test_claude_lb_launch.py`, `tests/unit/test_cli.py`
- Behavior: agents wait out LB restarts (up to ~2 minutes) instead of erroring;
  long-turn websocket sessions are no longer cut for slow pongs.
