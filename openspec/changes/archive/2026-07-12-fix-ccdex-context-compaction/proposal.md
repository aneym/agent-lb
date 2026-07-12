# Translate ccdex context overflow into a Claude Code compaction trigger

## Why

Two `ccdex` sessions (`77c31c13…`, `1af4c8ef…`) grew past the live model's
advertised context window (372k; first failures at ~371.5k input tokens) with
no compaction summary, then emitted 436 and 300+ repeated identical
`context_length_exceeded` (CLE) failures — a retry storm that never converged
and never compacted.

Root cause is the ccdex bridge's error translation. Claude Code's harness
decides whether a failed turn is a **reactive-compaction** trigger by
classifying the API error: only an error whose message contains the phrase
`prompt is too long` is categorized `prompt_too_long`, which drives
`tryReactiveCompact` (compact history, retry with `hasAttemptedReactiveCompact`).
Everything else falls through — a `529`/`overloaded_error` becomes
`server_overload` (retryable), and a generic `api_error` is not a compaction
signal, so the harness keeps re-sending the same over-limit request.

The bridge maps every upstream failure to `{"type":"api_error"}`
(`claude_codex_bridge.py:anthropic_error_from_response` for the pre-stream
non-streaming error, and `_ClaudeStreamState.consume` for the mid-stream
`response.failed`/`error` event). So an upstream `context_length_exceeded`
reaches Claude Code as a bare `api_error` that never matches `prompt is too
long` — the harness cannot compact and retries indefinitely.

The existing Cursor compatibility path (`_cursor_context_limit_usage_stream`)
does NOT apply here: Cursor compacts on synthetic over-limit **usage** numbers
returned as a successful empty turn. Claude Code has no such usage-based
trigger — a blank success would be a wrong (empty) assistant turn. The correct,
client-native fix is protocol-level error classification (option B), and it
alone stops the storm, so no identical-overflow breaker is added.

## What Changes

- The ccdex bridge detects an upstream context-overflow failure (upstream
  error `code == context_length_exceeded`, or a context-window/token-limit
  message) and translates it into an Anthropic-native `invalid_request_error`
  whose message contains `prompt is too long`, preserving the upstream HTTP
  status. Claude Code then classifies the turn `prompt_too_long` and reactive-
  compacts instead of retrying.
- Both surfaces are fixed with one shared classifier:
  - pre-stream / non-streaming error response (`anthropic_error_from_response`),
  - mid-stream `response.failed` / `error` event (`_ClaudeStreamState.consume`).
- Non-overflow upstream errors keep their existing `api_error` translation.
- Cursor behavior is untouched (its path lives in `app/modules/proxy/api.py`).

## Impact

- ccdex sessions that hit the context window compact and continue instead of
  storming identical CLE retries.
- Affected: `app/modules/proxy/claude_codex_bridge.py`,
  `tests/integration/test_ccdex_proxy.py`,
  `tests/unit/test_claude_codex_bridge.py`.
