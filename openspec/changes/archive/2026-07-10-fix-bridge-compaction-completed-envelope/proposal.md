# Change: fix remote-compaction v2 empty completed envelope on the HTTP bridge

## Why

Codex remote-compaction v2 counts compaction output items from the terminal
`response.completed` envelope (`response.output`). The upstream ChatGPT
websocket protocol streams output items as `response.output_item.done` frames
and sends a terminal `response.completed` whose `output` array is empty. When
agent-lb serves `/backend-api/codex/responses` through the HTTP bridge
(client HTTP → upstream websocket), it forwarded that terminal envelope
verbatim — so every bridge-routed compaction turn failed client-side with:

> remote compaction v2 expected exactly one compaction output item, got 0 from
> 0 output items

even though the compaction item itself streamed through correctly and the
request was billed (observed in production 2026-07-10: headless `codex exec`
lanes retried the same ~133k-token compact for hours across account rebinds,
burning 4–9k output tokens per attempt; reproduced with a live probe —
`output_item.done` carried the compaction item while `response.completed` had
`output: []`).

The earlier `fix-codex-remote-compaction-v2` change fixed the direct HTTP SSE
proxy path (public-stream normalizer re-typing the item); the bridge path has
no normalizer bug — it faithfully forwards an upstream envelope that is simply
empty on the websocket surface.

## What Changes

- The HTTP bridge tracks remote-compaction turns (request input contains a
  `compaction_trigger` item), accumulates streamed `compaction` output items
  per request (small cap bounds memory), and re-injects them into the terminal
  `response.completed` envelope when the upstream envelope's `output` array is
  empty or missing.
- Terminal envelopes that already carry output items pass through unmodified.
- Non-compaction turns are unaffected.

## Impact

- Affected specs: `responses-api-compat`
- Affected code: `app/modules/proxy/_service/support.py` (request-state
  fields + trigger detection helper),
  `app/modules/proxy/_service/http_bridge/request_submit.py` (flag set at
  submit), `app/modules/proxy/_service/http_bridge/upstream_events.py`
  (accumulate + re-inject).
