# Tasks

## 1. Implementation

- [x] 1.1 Alias map + delegation branch in `v1_messages`
- [x] 1.2 Shared `_ccgpt_messages_response` helper with `alias_effort` override
- [x] 1.3 Local token counting for alias models on `/v1/messages/count_tokens`

## 2. Validation

- [x] 2.1 Integration tests: alias effort pinning (medium/xhigh), plain-alias
      effort deferral, local count_tokens — at the `/v1/messages` surface
- [x] 2.2 `ruff check app clients` clean; full ccgpt proxy suite green
- [x] 2.3 Live service restarted and a real `/v1/messages` alias round-trip
      returns a Sol completion
- [x] 2.4 End-to-end: Claude Code subagent pinned to an alias completes a
      tool-use task through the bridge
