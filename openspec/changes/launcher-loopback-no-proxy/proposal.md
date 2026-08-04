# Launcher exports loopback NO_PROXY exclusions

## Why

`claude-lb-launch` exports `HTTPS_PROXY`/`https_proxy` pointing at the per-session MITM proxy so Claude's undici dispatcher tunnels api.anthropic.com through agent-lb. Every descendant process inherits that variable. Proxy-honoring HTTP clients in descendants (httpx in Hermes, curl, requests) then route loopback API calls — e.g. Hermes TUI chats targeting `http://127.0.0.1:2455/v1` — through the MITM proxy, whose HTTP handler answers non-Anthropic POSTs with a Python `http.server` 501 "Unsupported method ('POST')" page. This broke Hermes TUI model calls intermittently (observed 2026-07-09 through 2026-08-04) and was hard to diagnose because the failing client reports the loopback URL, not the proxy.

## What Changes

- When the launcher sets `HTTPS_PROXY`/`https_proxy`, it now also appends `127.0.0.1` and `localhost` to `NO_PROXY`/`no_proxy` (preserving existing entries, no duplicates), so descendants connect to loopback services directly.

## Capabilities

### Modified Capabilities

- `runtime-portability`: launcher environment contract additionally guarantees loopback proxy exclusions whenever the MITM proxy env is exported.

## Impact

`clients/claude-lb-launch` only. Claude's own routing is unaffected: its traffic targets `api.anthropic.com`, which is not excluded. Loopback-destined requests from Claude or descendants connect directly, which is the pre-proxy behavior.
