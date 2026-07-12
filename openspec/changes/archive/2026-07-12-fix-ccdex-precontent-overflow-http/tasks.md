# Tasks

- [x] Add `responses_to_claude_events` (dict-event translator) + `format_claude_events`, and re-express `responses_to_claude_sse` on top of them, keeping its output byte-for-byte identical
- [x] Add `split_startup_error`: bounded peek that returns a terminal `error` frame occurring before any `content_block_*` frame, else a replay iterator (buffered frames + rest of stream); close the upstream on the error path
- [x] Add `anthropic_status_for_error` mapping the Anthropic error type to an HTTP status (`invalid_request_error` → 400)
- [x] `/v1/ccdex/messages`: peek via `split_startup_error`; on a pre-content startup error return a JSON Anthropic error at the mapped HTTP status; otherwise stream/collect the replay unchanged (applies to both `stream` and non-stream turns)
- [x] Integration regression: pre-content `response.failed` overflow returns HTTP 400 `invalid_request_error` (`prompt is too long`), no `message_start` in body; same via top-level Codex `error` frame; genuine mid-stream overflow (content then `response.failed`) stays HTTP 200 SSE with the `error` event and no trailing `message_delta`/`message_stop`
- [x] Unit tests: `split_startup_error` catches pre-content error / replays on content-first / replays empty success; `anthropic_status_for_error` type→status mapping
- [x] Gates: `ruff check` on changed files; ccdex + bridge + http-bridge suites; app import; `openspec validate --specs`
