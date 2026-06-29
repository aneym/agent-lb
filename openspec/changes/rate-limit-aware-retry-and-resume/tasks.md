# Tasks

## 1. Proxy

- [x] 1.1 Add `retry_at` to `AnthropicProxyError`; populate it on quota-cooldown and selection-failure paths.
- [x] 1.2 Return 429 `rate_limit_error` with `retry-after` + `anthropic-ratelimit-unified-reset` headers on `/v1/messages` when reset is known.
- [x] 1.3 Emit SSE `event: error` (or JSON envelope) on mid-stream failure instead of truncating.
- [x] 1.4 Include `retryAt`/`retryAfterSeconds` + `retry-after` header on session-route error envelopes.
- [x] 1.5 Accept epoch-seconds/milliseconds upstream reset headers.
- [x] 1.6 Drain non-streaming `/v1/messages` bodies before sending headers so upstream errors preserve their real HTTP status and Anthropic error envelope.
- [x] 1.7 Pass dashboard reset preference into Anthropic selection with the Claude primary (5-hour) window.

## 2. Launcher

- [x] 2.1 Preflight wait-and-reclaim loop honoring `CLAUDE_LB_WAIT_FOR_LIMIT` / `CLAUDE_LB_WAIT_MAX_SECONDS`.
- [x] 2.2 Headless auto-resume: inject `--session-id`, probe LB after failure, wait, `claude --resume`, bounded attempts.
- [x] 2.3 Sync the vendored launcher to `~/.claude/bin/claude-smart-launch`.

## 3. Verification

- [x] 3.1 Integration tests: 429 headers, session-route retryAt, mid-stream SSE error event.
- [x] 3.1a Integration test: non-streaming upstream 529 returns HTTP 529 `overloaded_error`, not HTTP 200.
- [x] 3.2 Unit tests: launcher retry-metadata parsing, wait decision, resume command.
- [x] 3.3 Ruff + full Anthropic proxy test module green.
- [x] 3.4 Restart live service and demonstrate wait/auto-launch end to end.
- [x] 3.5 Validate the OpenSpec change.
  - 2026-06-14: `npx --yes @fission-ai/openspec@latest validate rate-limit-aware-retry-and-resume --strict` -> valid.
