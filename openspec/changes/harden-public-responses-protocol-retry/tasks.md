## 1. Streaming Retry Contract

- [x] 1.1 Pass an explicit public OpenAI-contract retry option from Responses routes into the streaming service and HTTP bridge fallback path.
- [x] 1.2 Buffer lifecycle-only SSE events before semantic output when the public retry option is enabled.
- [x] 1.3 Retry transient lifecycle-only terminal failures within the existing same-account retry and account-failover budgets.
- [x] 1.4 Extend the native HTTP bridge terminal-error replay path for protocol-only GPT `upstream_stream_truncated` failures while preserving backend Codex behavior.

## 2. Regression Coverage

- [x] 2.1 Add an integration test proving public `/v1/responses` retries a lifecycle-only `stream_incomplete` attempt and emits the successful attempt.
- [x] 2.2 Keep or extend coverage proving backend `/backend-api/codex/responses` still surfaces failures after an emitted lifecycle event.
- [x] 2.3 Add native HTTP bridge coverage for lifecycle-only retry and no replay after visible output.
- [x] 2.4 Run targeted tests plus OpenSpec validation for the change.
