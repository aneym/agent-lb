# Fix event-loop stall resilience end to end

## Why

On 2026-07-16 the service stalled in bursts (event_loop_lag warnings of
1–14s, 60 in one hour) while remaining up, so `cc`'s single 0.35s
`/health/ready` probe kept landing inside stall windows and forced sessions
onto plain-claude fallback. Live SIGUSR2 captures during a stall showed the
loop frozen inside anyio `CancelScope` cancellation delivery under starlette
`BaseHTTPMiddleware` (fixed upstream in anyio 4.14.2, issue #1111), and a
second stall unblocked mid `idle_prune` — the http-bridge registry scan that
runs synchronously under the global bridge lock on every session get-or-create.
Separately, the automatic stall forensics promised by
`add-stall-forensics-loop-lag` never fired (SIGUSR2 is manual-only), and the
front proxy once died entirely from an unhandled ENOSPC on its log stream.

## What Changes

- Upgrade anyio 4.13.0 → 4.14.2 for the upstream fix to CPU spin in
  `CancelScope` cancellation delivery under improper scope nesting (the
  starlette `BaseHTTPMiddleware` shape this app runs).
- Throttle `_prune_http_bridge_sessions_locked` to at most one full-registry
  scan per 5s (`_HTTP_BRIDGE_IDLE_PRUNE_MIN_INTERVAL_SECONDS`); idle TTLs are
  120s/900s so eviction timing is unaffected.
- Add an event-loop stall watchdog thread to `EventLoopLagMonitor`: it watches
  a heartbeat stamped by the in-loop sampler and appends a timestamped
  all-threads faulthandler dump to `~/.agent-lb/forensics/py-stacks.log` while
  a stall ≥5s is still in progress (rate-limited to one dump per 60s), so
  stalls self-document without manual SIGUSR2.
- `cc` launcher: `prepare_interactive_endpoint` probes `/health/ready` 3 times
  spaced 1s apart instead of once, so a short stall burst no longer forces
  plain-claude fallback; the healthy path still returns on the first probe.
- Front proxy (`scripts/agent-lb-front.mjs`): log writes are best-effort; an
  ENOSPC/EPIPE on the stdout stream can no longer crash the process holding
  port 2455.

Round 2 (same day): post-fix watchdog captures showed the residual 0.5–2s
lags were many small synchronous costs, each caught in live stack dumps:
`ssl.create_default_context()` loading the macOS trust store on the loop for
every upstream websocket connect, permessage-deflate zlib-encoding multi-MB
prompt frames on the loop, and GC pauses during large SSE `json.loads`.
Those lag bursts overflow the kernel accept backlog (`somaxconn=128`), so the
front proxy sees refused upstream connects and holds/retries at 500ms — which
a 0.35s `cc` probe can never ride out.

- Upstream websocket connects share one process-wide `SSLContext` and disable
  permessage-deflate (`compression=None`).
- `cc` local `/health/ready` probe timeout default raised 0.35s → 1.5s so one
  front hold-retry cycle fits inside a probe attempt; raised again 1.5s → 3.0s
  (local and remote) after 2026-07-17 host overload (load avg 260, ready
  probes spiking to ~1.7s) timed out all three 1.5s attempts against a live LB.

## Impact

- Affected specs: proxy-runtime-observability, deployment-installation
- Affected code: `app/modules/proxy/_service/http_bridge/mixin.py`,
  `app/modules/proxy/_service/http_bridge/protocol.py`,
  `app/modules/proxy/service.py`, `app/core/forensics/__init__.py`,
  `app/core/resilience/event_loop_lag_monitor.py`, `clients/claude-lb-launch`,
  `scripts/agent-lb-front.mjs`, `uv.lock`
- Follow-up (not in this change): convert the `@app.middleware("http")` /
  `BaseHTTPMiddleware` layers to pure ASGI middleware to remove per-request
  cancel-scope machinery from streaming paths entirely.
