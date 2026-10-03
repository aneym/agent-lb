## ADDED Requirements

### Requirement: Streaming requests record time to first model output
The proxy SHALL set `request_logs.latency_first_token_ms` once, in milliseconds from the request's existing monotonic start, when the first upstream event carrying model output is observed. For Anthropic SSE that event is the first `content_block_start` or `content_block_delta`. For OpenAI Responses streams, including HTTP streaming, the HTTP bridge, and websocket relay, that event is the first `response.output_item.added` or any event whose type ends in `.delta`. A later output event SHALL NOT overwrite the value. Non-streaming requests SHALL leave the column null. Forwarded response bytes SHALL stay unchanged.

#### Scenario: Anthropic content block after message start
- **WHEN** an Anthropic streaming response emits `message_start` and later `content_block_start`
- **THEN** the request log stores `latency_first_token_ms` from the request start through that content block
- **AND** the forwarded SSE bytes match the upstream bytes

#### Scenario: OpenAI tool call with no text delta
- **WHEN** an OpenAI streaming response's first model output is `response.function_call_arguments.delta`
- **THEN** the request log stores `latency_first_token_ms`
- **AND** a `response.created` event before that output does not set it

#### Scenario: Non-streaming response
- **WHEN** a request completes without a stream
- **THEN** `latency_first_token_ms` stays null

### Requirement: Latency command summarizes first-output time
`agent-lb latency` SHALL read request logs without writing. `--since` SHALL accept `Nh`, `Nd`, or an ISO timestamp and SHALL default to `24h`. `--by` SHALL accept any subset of `provider`, `account`, and `hour`, and SHALL default to one table for each. Each group SHALL report the number of rows, the number with TTFT, p50 and p95 TTFT in milliseconds, and p50 total latency in milliseconds. Account labels SHALL be the first 8 characters of `account_id`. Hour labels SHALL be UTC timestamps with a `Z` suffix. The command SHALL also report how many rows in the window have error code `upload_admission_timeout` or `upload_admission_rejected`. `--json` SHALL emit the same figures. `--db` or `AGENT_LB_DATABASE_URL` SHALL select the database, and the query SHALL run on PostgreSQL and SQLite.

#### Scenario: JSON report for a seeded window
- **WHEN** an operator runs `agent-lb latency --json` against request logs in the window
- **THEN** each requested group includes streaming count, TTFT count, p50 TTFT, p95 TTFT, and p50 total latency
- **AND** the output includes the upload-admission timeout and rejected counts
