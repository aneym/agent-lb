# Surface pre-content ccdex overflow as an HTTP error, not a 200 SSE stream

## Why

The prior change (`fix-ccdex-context-compaction`, archived) made the ccdex bridge
translate a context overflow into an Anthropic `invalid_request_error` with the
`prompt is too long` phrase Claude Code needs to reactive-compact. That fixed the
error *shape*, but a fresh end-to-end ccdex stress session (`8c7ba64d…`) still
failed to compact on turn 4: turns 1–3 succeeded, turn 4 crossed the context
window and the CLI produced only `init`, a synthetic "previous response had no
visible output", then `error_during_execution` with `last_content_type=none` and
**zero** `compact_boundary`.

Root cause is *how* the error reaches the harness. The live endpoint correctly
emits an Anthropic `invalid_request_error` — but as an HTTP **200** Server-Sent
Events stream: `[message_start, error]`. Claude Code only classifies a turn as
`prompt_too_long` (its reactive-compaction trigger) when the **HTTP request
itself fails before the assistant turn is created**. An `error` event delivered
after `message_start` under HTTP 200 is silently dropped — the harness has
already created an (empty) assistant turn, so it aborts instead of compacting.

Why the overflow arrives in-band at all: `_stream_responses` runs a pre-stream
HTTP probe that only inspects the **first** upstream item. On an over-limit turn
the upstream emits `response.created` fast (before/at the start of the reasoning
phase), so the probe sees a non-error first item and commits to a streaming
HTTP 200. The terminal `context_length_exceeded` then arrives as a later
`response.failed` / top-level `error` frame — past the probe — and the bridge
renders it as `message_start` + `error` under the already-committed 200.

## What Changes

- The ccdex handler peeks the translated Messages stream with a **bounded**
  startup buffer: it holds frames only until the first `content_block_*` frame or
  the first terminal `error` frame, whichever comes first (one or two frames in
  practice; released the instant real content appears, so TTFT to first visible
  token is unaffected).
- A terminal error that arrives **before any assistant content** is surfaced as a
  non-200 HTTP error response (HTTP 400 for context overflow) carrying the
  Anthropic error envelope — never as an HTTP 200 SSE stream beginning with
  `message_start`. This is what drives Claude Code's reactive compaction.
- A terminal error that arrives **after** visible content has streamed remains a
  genuine mid-stream failure: an in-band Anthropic `error` event under HTTP 200
  with no trailing successful `message_delta` / `message_stop`.
- Both in-band nestings are covered: the `response.failed` envelope and the
  ChatGPT-backed Codex top-level `error` frame (detail fields on the event root).
- The existing pre-stream non-streaming error path is unchanged (it already
  returns an Anthropic HTTP error via `anthropic_error_from_response`).

## Impact

- ccdex sessions that cross the context window mid-reasoning now receive an
  HTTP 400 overflow, reactive-compact, and continue — instead of aborting the
  turn on a dropped in-band error.
- Affected: `app/modules/proxy/claude_codex_bridge.py`,
  `app/modules/proxy/api.py`, `tests/integration/test_ccdex_proxy.py`,
  `tests/unit/test_claude_codex_bridge.py`.
