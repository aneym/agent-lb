# Tasks

## 1. Record first output

- [x] 1.1 Stamp Anthropic streaming success and `stream_error_*` request logs
      from the first content-block start or delta, using the existing SSE parse.
- [x] 1.2 Stamp OpenAI http streaming, http bridge, and websocket relays from
      `is_first_output_event`, leaving text-delta detection in place for other uses.

## 2. Report

- [x] 2.1 Add `agent-lb latency` (`--since`, `--by`, `--json`, `--db`) over
      request_logs on PostgreSQL and SQLite.

## 3. Validation

- [x] 3.1 Anthropic streaming delay, OpenAI function-call-first stream, and
      latency CLI percentile tests pass with ruff.
