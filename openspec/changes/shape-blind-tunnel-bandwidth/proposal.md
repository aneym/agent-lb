## Why

All agent HTTPS traffic on a machine funnels through the launcher's shim proxies (per-session shims plus the shared desktop shim). Blind CONNECT tunnels relay at line rate, so a single bulk download by any tunneled client saturates the access link and starves the latency-sensitive Anthropic API streams every agent depends on (observed 2026-08-22: one 373+ MB GitHub Pages pull at 25-30 Mbps collapsed a weak Wi-Fi link; API traffic at the time was under 3 Mbps). Heavy tunnel flows are also unattributable without packet capture, which caused the incident to be misdiagnosed as agent-lb itself flooding the network.

## What Changes

- Shape the aggregate blind-tunnel relay rate per shim proxy process with earliest-departure-time pacing (default 25 Mbps combined up+down, `CLAUDE_LB_TUNNEL_RATE_MBPS` override, non-positive or non-finite value disables shaping).
- Never shape the MITMed api.anthropic.com path; agent/model concurrency and API throughput stay unlimited.
- Exempt small writes (<= 8KB) from pacing sleeps while the booked backlog stays inside one burst window, so Remote Control websocket frames and OAuth exchanges see no added latency; the exemption still debits the schedule so small-chunked streams cannot dodge shaping.
- Log a one-line summary to stderr for any tunnel that relayed at least 8 MB, so heavy flows are attributable from the shim log.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `claude-desktop-proxy`: Blind-tunnel relay traffic is rate-shaped and heavy tunnels are logged; the intercepted API path is explicitly exempt.

## Impact

- Affects `clients/claude-lb-launch` (`_TunnelBucket`, `_splice`, `_ProxyHandler._tunnel`, `run_lb_proxy`) for both per-session shims and the shared desktop shim.
- No server, schema, or plist changes; the default rate ships in code so deploy stays on `launchctl kickstart -k`.
