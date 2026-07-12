## Context

The prior change (`fix-ccdex-context-compaction`, archived) made the ccdex bridge
translate an upstream `context_length_exceeded` into an Anthropic
`invalid_request_error` whose message contains `prompt is too long` — the phrase
Claude Code's harness classifies as `prompt_too_long` to gate `tryReactiveCompact`.
That corrected the error *shape*. A fresh end-to-end ccdex stress session
(`8c7ba64d-2d5f-4740-b417-6d3a4a11a5f0`) still failed to compact on turn 4: turns
1–3 succeeded (input reached 306155 on turn 3), turn 4 crossed the context window
and the CLI produced only `init`, a synthetic "previous response had no visible
output", then `error_during_execution` with `ede_diagnostic last_content_type=none`
and **zero** `compact_boundary`.

Root cause is the *delivery channel*, not the payload. Claude Code only classifies
a turn as `prompt_too_long` when the **HTTP request itself fails before the
assistant turn is created**. An `error` SSE event delivered *after* `message_start`
under HTTP 200 is silently dropped — the harness has already created an (empty)
assistant turn and aborts (`error_during_execution`) instead of compacting.

Why the overflow arrives in-band on this route: `_stream_responses`
(`app/modules/proxy/api.py`) runs a pre-stream probe (`_probe_stream_startup_error`)
that inspects only the **first** upstream item. On an over-limit turn the upstream
emits `response.created` fast (before/at the start of the reasoning phase), so the
probe sees a non-error first item and commits to an HTTP 200 `StreamingResponse`.
The terminal `context_length_exceeded` then arrives as a later `response.failed`
or top-level Codex `error` frame — past the probe — and `responses_to_claude_sse`
renders it as `[message_start, error]` under the already-committed 200. Live-endpoint
request logs for the failing turn showed CLE at ids `15267, 15271, 15274, 15279`,
each an HTTP 200 SSE `[message_start, error]` with `invalid_request_error` +
`prompt is too long` and no success terminal — exactly the dropped shape.

Constraints: change only the ccdex handler + bridge translation layer; do not touch
the shared Responses stream, the pre-stream probe, the Cursor path, or account
routing. Do not buffer the stream unboundedly or delay the first visible token.

## Goals / Non-Goals

**Goals:**
- A terminal overflow occurring before any assistant content reaches Claude Code
  as a non-200 HTTP response (HTTP 400 for context overflow), so the harness
  classifies `prompt_too_long` and reactive-compacts.
- A terminal error occurring after visible content stays a genuine mid-stream
  failure: an in-band Anthropic `error` event under HTTP 200 with no trailing
  successful `message_delta`/`message_stop`.
- Cover both in-band nestings (`response.failed`, top-level Codex `error` frame)
  and both the streaming and non-streaming (`collect_claude_message`) turn paths.
- Keep the peek bounded so TTFT-to-first-visible-token and memory are unaffected.

**Non-Goals:**
- Changing the pre-stream HTTP probe or its timeout (`_probe_stream_startup_error`).
- Proactive/token-count-based compaction (the harness owns that).
- Altering non-overflow error translation or any other route.

## Decisions

### Bounded startup peek in the bridge

`split_startup_error(events)` (in `claude_codex_bridge.py`) consumes the translated
Anthropic Messages **event** stream and buffers frames until the first of:

- a `content_block_start` / `content_block_delta` frame (`_CONTENT_FRAME_TYPES`) —
  the assistant turn now has real, visible content; **or**
- a terminal `error` frame.

Whichever comes first ends the peek. If the error wins, it is returned to the
handler (stream is closed via `aclose()`) so the handler can surface an HTTP error
and drop the stream. If content wins, `split_startup_error` returns a `replay`
async iterator that re-emits the buffered frames then the untouched remainder of
the stream — so genuine mid-stream failures, empty successes, and normal turns are
byte-for-byte unchanged.

To keep this testable and reusable, the translator was factored into
`responses_to_claude_events` (dict events) with `responses_to_claude_sse` and the
new `format_claude_events` layered on top; `responses_to_claude_sse` output is
unchanged.

### HTTP status mapping

`anthropic_status_for_error(frame)` maps the Anthropic error `type` to an HTTP
status (`invalid_request_error → 400`, `authentication_error → 401`,
`rate_limit_error → 429`, `overloaded_error → 529`, `api_error → 502`, default
`502`). Context overflow is `invalid_request_error`, so a pre-content overflow
becomes **HTTP 400** carrying `{"type":"error","error":{...}}` — the same envelope
the pre-stream non-streaming path already returns.

### Handler wiring

`v1_ccdex_messages` peeks once via `split_startup_error(responses_to_claude_events(...))`.
On a pre-content startup error it returns `JSONResponse(content=frame,
status_code=anthropic_status_for_error(frame))` — for both streaming and
non-streaming requests (the peek runs before the `payload.stream` branch). Otherwise
it formats the replay to SSE and streams (or collects) as before.

## TTFT and memory bounds

- **Buffer size:** the translator emits nothing for reasoning/keepalive frames, so
  between `message_start` and first output the buffer holds at most the single
  `message_start` frame. The peek releases on the first `content_block_*` frame, so
  the buffer is 1–2 frames in the streaming happy path and never grows with
  reasoning length or response size. The only case buffered to stream end is a
  genuinely empty completion (3 frames: `message_start`/`message_delta`/`message_stop`).
- **TTFT:** `message_start` carries no visible content, so holding it until first
  content does not change *perceived* time-to-first-token — the delay equals the
  unavoidable time-to-first-output-token (reasoning happens upstream regardless).
  Content is forwarded the instant it appears; keepalives are re-injected by the
  handler's own `inject_sse_keepalives`, so idle-gap heartbeats are preserved.
- **Cleanup:** on the error path the upstream event generator is `aclose()`d so the
  abandoned `StreamingResponse.body_iterator` chain is released rather than left to GC.

## Risks / Trade-offs

- **Non-overflow pre-content errors now become HTTP (e.g. 502) instead of 200 SSE.**
  This is strictly more correct (a 200 followed by an error event is a protocol
  smell) and is what Claude Code expects for a failed request; overflow → 400 is the
  load-bearing case and is covered by tests.
- **`content_block_start` counts as "content" even if no delta follows.** A tool
  call or an opened text block is legitimate assistant structure; treating it as the
  commit point is intentional and matches the harness's turn-creation boundary.

## E2E acceptance evidence

- **Reproduction:** session `8c7ba64d…` turn 4 — `last_content_type=none`, zero
  `compact_boundary`; live logs show HTTP 200 SSE `[message_start, error]` at CLE
  ids `15267/15271/15274/15279`. After this change a pre-content overflow returns
  HTTP 400 before any `message_start`, which is the shape Claude Code compacts on.
- **Fresh-session pass:** disposable session `0a3e9fce…` ran four real `ccdex -p`
  turns against the restarted local service. Turns 1–3 returned the exact visible
  checkpoints `CHECKPOINT_ONE`, `CHECKPOINT_TWO`, and `POST_COMPACTION_OK`. Turn 4
  crossed the upstream ceiling, emitted one `compact_boundary`, retried once, and
  returned the exact visible response `AFTER_REAL_COMPACTION` with exit 0 and no
  errors. Read-only request-log range `15820..15845` contained exactly one
  `context_length_exceeded` row (`15841`), rather than the prior four-request loop.
- **Regression (red→green):** new tests first failed on `ImportError`
  (`anthropic_status_for_error`), confirming red. Green: focused
  `tests/unit/test_claude_codex_bridge.py` + `tests/integration/test_ccdex_proxy.py`
  = 26 passed; full bridge regression incl. `test_http_responses_bridge.py` and
  `test_multi_instance_bridge.py` = 113 passed.
- **Boundary coverage:** integration — pre-content `response.failed` overflow → HTTP
  400 (`prompt is too long`, no `message_start` in body); pre-content top-level Codex
  `error` frame → HTTP 400; content-then-`response.failed` → HTTP 200 SSE with the
  `error` event, `partial answer` content, and no trailing `message_delta`/`message_stop`.
  Unit — `split_startup_error` catches pre-content error / replays on content-first /
  replays empty success; `anthropic_status_for_error` type→status mapping.
- **Gates:** `ruff` clean on all changed files; app import clean; `openspec validate
  --strict` on this change and `--specs` (33/33) pass.
