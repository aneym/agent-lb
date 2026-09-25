## Why

Claude workflows fan out hundreds of agents at once, and agent-lb has to carry
that as long as the upstream accounts can. A staircase load eval (25, 50, 100,
200 concurrent tiny requests per path, `scripts/parallel_load_eval.py`) and 72
hours of request and stall logs found three local limits that were not
protecting against any upstream limit:

- The shared outbound HTTP pool allowed 50 connections per host. Every
  Anthropic stream goes to one host, so the 51st parallel stream waited for a
  slot, and aiohttp counts that wait against the 8 s connect timeout. Anthropic
  latency stepped up by about a second per 50 requests in the eval.
- With the gaming-mode upload cap on, the token bucket released whatever had
  trickled in since the last call, about one byte, so each upload became a busy
  loop of 1-byte TLS writes on the event loop. 247 of 255 stall dumps on
  2026-09-23 were in that path, with stalls up to 13.9 s.
- The upstream edge answers a burst of websocket handshakes from one address
  with a bare 403 on every account at once (127 of 200 parallel Codex requests).
  agent-lb surfaced that 403 to the agent as a hard failure.

## What Changes

- Default the shared HTTP pool to 1024 connections in total with no per-host
  cap. The total stays as a file-descriptor guard.
- Release paced upload bytes in chunks of at least one TLS record (16 KiB), so
  the cap keeps its rate without holding the event loop.
- Retry an upstream websocket handshake that the edge rejects with a bare 403,
  using jittered exponential backoff capped at 30 s per wait, for up to three
  minutes from the first refusal. The edge limits the rate of new handshakes,
  not how many stay open: 210 sockets opened 70 at a time and held open drew no
  refusal, while a single burst of 200 kept 30 refused for more than the 30 s
  that a first six-attempt version allowed. A 403 that carries an OpenAI error
  body is still surfaced on the first answer.
- Add `scripts/parallel_load_eval.py`, the staircase eval that produced this
  evidence. It writes receipts under `~/.agent-lb/evals/parallel-load/receipts/`.

## Capabilities

### Modified Capabilities

- `outbound-http-clients`

## Impact

`app/core/config/settings.py`, `app/core/upload_throttle.py`,
`app/core/clients/proxy_websocket.py`. There are no schema or API shape changes.
Operators who set `AGENT_LB_HTTP_CONNECTOR_LIMIT` or
`AGENT_LB_HTTP_CONNECTOR_LIMIT_PER_HOST` keep their values.
