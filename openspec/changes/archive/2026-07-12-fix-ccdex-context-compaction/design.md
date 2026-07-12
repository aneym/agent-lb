## Context

`/v1/ccdex/messages` runs the real Claude Code harness over the GPT-5.6 Sol
Responses bridge (`app/modules/proxy/claude_codex_bridge.py`). When a turn's
input exceeds the model context window, the upstream Responses path fails with a
`context_length_exceeded` error. The bridge translated every upstream failure to
an Anthropic `{"type":"api_error"}` envelope.

Claude Code's harness (verified against the installed
`@anthropic-ai/claude-code` bundle) classifies a failed turn's API error and
reactive-compacts on exactly one category. The classifier, in order:

- `status === 429` → `rate_limit`
- `status === 529` or message contains `"type":"overloaded_error"` → `server_overload` (retryable)
- `message.toLowerCase().includes("prompt is too long")` → `prompt_too_long`

Only `prompt_too_long` gates `tryReactiveCompact` (compact transcript, retry with
`hasAttemptedReactiveCompact`, `transition.reason = "reactive_compact_retry"`).
A bare `api_error` matches nothing, so the harness re-sends the identical
over-limit request — the observed 300–436 repeated CLE storm in sessions
`77c31c13…` and `1af4c8ef…`, which grew past the advertised 372k window (first
failures ~371.5k) with no compaction summary.

Constraints: change only the ccdex bridge translation; do not touch the Cursor
compatibility path (`app/modules/proxy/api.py`), the shared Responses stream, or
account routing. Non-normative client evidence lives in `context.md`.

## Goals / Non-Goals

**Goals:**
- Upstream context overflow reaches Claude Code as an error the harness
  classifies `prompt_too_long`, so it compacts and continues.
- Cover both bridge error surfaces (pre-stream and mid-stream) with one shared
  classifier.
- Leave non-overflow error translation and Cursor behavior unchanged.

**Non-Goals:**
- Proactive/token-count-based compaction (the harness owns that; out of scope).
- Any synthetic-usage success turn for Claude Code.
- A retry/circuit breaker for identical overflows.
- Changing the HTTP status the caller emits (status is preserved).

## Decisions

**Decision 1 — Fix by error classification (option B), not synthetic usage
(option A).** The Cursor path returns a successful empty turn carrying synthetic
over-limit *usage*; Cursor compacts on those numbers. Claude Code has no
usage-based trigger — a blank success would be a wrong empty assistant turn and
would not compact. The client-native trigger is the error message substring
`prompt is too long`, so the correct fix is protocol-level: translate overflow
to an Anthropic `invalid_request_error` whose message contains that phrase.
Rejected: extending `_cursor_context_limit_usage_stream` to ccdex (wrong client
contract).

**Decision 2 — Message must carry `prompt is too long`; type is
`invalid_request_error`.** The harness matches only the message substring, but
the real Anthropic overflow envelope is `invalid_request_error`, and emitting
that type also keeps the error clear of the `server_overload` (retryable)
branch. `_anthropic_overflow_message` preserves upstream detail and guarantees
the canonical phrase: it returns upstream text verbatim when it already contains
the phrase, otherwise prefixes `Prompt is too long: <detail>`, falling back to
`Prompt is too long` when detail is empty.

**Decision 3 — One shared classifier, both surfaces.** Overflow can appear at
two seams, and both previously produced `api_error`:
- *Pre-stream / non-streaming error* — `anthropic_error_from_response(status,
  body)`, invoked by `v1_ccdex_messages` on the non-`StreamingResponse` branch
  (the primary surface: over-limit input rejected before streaming). It now
  parses upstream `error.code` in addition to `error.message`.
- *Mid-stream* — `_ClaudeStreamState.consume` on `response.failed`/`error`; it
  now also reads `response.error` (the `response.failed` nesting) and the error
  `code`.
Both route through `_anthropic_error_payload(code, message)`, which returns the
`invalid_request_error`/`prompt is too long` envelope when
`_is_context_overflow_error` is true and the existing `api_error` envelope
otherwise. Detection primary signal is upstream `code ==
"context_length_exceeded"`; a message-marker list (`context window`, `input
token limit`, …) is a defensive fallback for envelopes that omit the code.
Rejected: fixing only the pre-stream surface (mid-stream overflow would still
storm).

**Decision 4 — No breaker (revisited).** Because the protocol fix makes the
harness compact instead of re-sending, the storm cannot recur from an identical
request; a bounded identical-overflow breaker would be dead code. Add one only
if a future fix that *cannot* prevent the storm is adopted.

## Risks / Trade-offs

- [Harness changes its `prompt_too_long` trigger string] → The trigger is the
  documented Anthropic overflow phrasing and is asserted in tests; a client-side
  change would surface as the regression test's failure, not silent drift.
- [False positive: a non-overflow error whose message contains a marker word]
  → Primary detection is the exact upstream `code`, not fuzzy text; markers are
  a narrow fallback list, and the non-overflow path is covered by an explicit
  "stays api_error" test.
- [Mid-stream `response.failed` error shape varies upstream] → `consume` now
  checks both `event.error` and `response.error`; unknown shapes fall back to
  the prior default message and `api_error`, i.e. no worse than before.

## Test strategy

- Failing regression at the real `/v1/ccdex/messages` boundary
  (`tests/integration/test_ccdex_proxy.py`): a fake `_stream_responses` returns
  the *real* `_stream_startup_error_response` built from a
  `context_length_exceeded` `ProxyResponseError`, exercising the true handler +
  translation. Red on current code (`api_error` != `invalid_request_error`),
  green after the fix. A companion route-level test drives a mid-stream
  `response.failed` `context_length_exceeded` SSE event through the same
  boundary and asserts the `invalid_request_error`/`prompt is too long` event
  with no trailing `message_delta`/`message_stop` success. Another companion
  test asserts a non-overflow error stays `api_error`.
- Unit tests (`tests/unit/test_claude_codex_bridge.py`):
  `anthropic_error_from_response` overflow → `invalid_request_error` with the
  phrase, non-overflow → unchanged `api_error`; and `responses_to_claude_sse`
  maps a mid-stream `context_length_exceeded` event to the overflow envelope.
- Gates: `ruff check app clients tests`, targeted ccdex/bridge + Cursor +
  http-bridge suites (no regression), app import, strict OpenSpec validation.

## Migration Plan

Pure server-side translation change; no schema, config, or client migration.
Deploy is the standard validated push + live-service restart. Rollback is
reverting the bridge diff — the harness then returns to `api_error` (prior
behavior).

## Open Questions

None.
