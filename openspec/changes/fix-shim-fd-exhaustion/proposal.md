## Why

The Claude Desktop embedded client opens CONNECT tunnels to the shared desktop shim and abandons them without closing. Each abandoned connection parked a handler thread forever in an untimed read, holding its fd; the long-lived shared shim exhausted launchd's 256-fd soft cap within hours (observed 2026-07-21: 245 leaked loopback fds in under 5 hours), and every subsequent upstream call failed with EMFILE surfaced as 502 `claude_lb_shim_error [Errno 24] Too many open files`.

## What Changes

- Reap shim client connections idle at the HTTP layer (pre-request reads, TLS handshakes, wedged writes) after a 600s timeout; established blind CONNECT tunnels such as the Remote Control websocket remain exempt and stay open indefinitely while idle.
- Raise the shim process's soft fd limit toward macOS OPEN_MAX (10240, bounded by the hard limit) at proxy startup, best-effort.
- Scope the shim's upstream connect retry loop to the connect phase only: a client hangup while copying a completed upstream response is no longer classified as a retryable connect failure, so an already processed Messages request is never re-sent.
- Close upstream responses explicitly after copying instead of leaving their sockets to the garbage collector.

## Capabilities

### Modified Capabilities

- `claude-desktop-proxy`: Add shim connection-hygiene requirements (idle client reaping, fd headroom, connect-phase-only retries).

## Impact

`clients/claude-lb-launch` only (both the per-session launcher shim and the shared desktop proxy paths). Server routing, settings ownership, and same-host routing rules are unchanged.
