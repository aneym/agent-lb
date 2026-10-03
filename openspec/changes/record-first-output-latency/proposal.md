# Record first-output latency on every stream

## Why

`request_logs.latency_first_token_ms` is empty for Anthropic streams and for
OpenAI turns that reason or call a tool before any text delta. A postmortem
cannot separate upstream time-to-first-output from time spent inside agent-lb.

## What Changes

- TTFT is milliseconds from the request's existing monotonic start
  (`started_at` / `request_started_at`, so admission wait and retries count)
  to the first upstream event that carries model output.
- Anthropic: the first `content_block_start` or `content_block_delta`.
- OpenAI Responses (http streaming, http bridge, websocket): the first
  `response.output_item.added` or any event type ending in `.delta`.
- The value is set once and is left null for non-streaming requests.
- `agent-lb latency` prints read-only p50/p95 TTFT and p50 total latency
  grouped by provider, account, and hour, plus upload-admission counts.

## Impact

- Affected specs: proxy-runtime-observability
- Affected code: Anthropic stream logging, OpenAI stream/bridge/websocket
  first-token detection, `app/latency_cli.py`, `app/cli.py`
