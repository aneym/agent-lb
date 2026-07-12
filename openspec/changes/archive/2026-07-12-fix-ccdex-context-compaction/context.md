# Context

## Client-facing semantics (confirmed against the installed Claude Code harness)

Claude Code classifies a failed turn's API error and only compacts on one
category. From the installed `@anthropic-ai/claude-code` bundle:

- `status === 429` → `rate_limit`
- `status === 529 || message includes '"type":"overloaded_error"'` → `server_overload` (retryable)
- `message.toLowerCase().includes("prompt is too long")` → `prompt_too_long`

`prompt_too_long` is the only category that drives reactive compaction:
`isWithheldPromptTooLong(...)` gates `tryReactiveCompact(...)`, which compacts
the transcript and retries with `hasAttemptedReactiveCompact` and
`transition.reason = "reactive_compact_retry"`. The trigger is a pure
case-insensitive substring match on the error message; the Anthropic SDK builds
that message from the error envelope body, so any envelope whose message
contains `prompt is too long` triggers it.

Consequences for the bug:
- A bare `api_error` (the bridge's previous behavior) matches none of the
  categories, so the harness neither compacts nor treats it as terminal for the
  turn — it re-sends the identical over-limit request (the 300–436 CLE storm).
- Returning `invalid_request_error` with a `prompt is too long` message makes
  the harness compact and retry, converging the session.

## Why not the Cursor path

`_cursor_context_limit_usage_stream` returns a successful empty turn with
synthetic over-limit **usage**; Cursor compacts on those usage numbers. Claude
Code has no usage-based compaction trigger, so a blank success would be a wrong
empty assistant turn. Hence option B (error classification), not option A.

## Why no identical-overflow breaker

The protocol fix alone stops the storm: once the harness sees `prompt is too
long` it compacts instead of re-sending. A bounded breaker would be dead code,
so none is added (only add one if a fix that cannot prevent the storm is ever
adopted).

## Surfaces

The bridge maps upstream failures in two places, both fixed with one shared
classifier in `claude_codex_bridge.py`:

- `anthropic_error_from_response(status_code, body)` — the pre-stream
  non-streaming error response returned by `_stream_responses` and handled at
  `api.py` (`v1_ccdex_messages`, the `not isinstance(upstream, StreamingResponse)`
  branch). This is the primary surface for over-limit input rejected before
  streaming.
- `_ClaudeStreamState.consume` `response.failed`/`error` branch — a mid-stream
  overflow event.

Upstream context-overflow envelope shape (observed in tests):
`{"error":{"type":"invalid_request_error","code":"context_length_exceeded","param":"input","message":"…"}}`
at HTTP 400.
