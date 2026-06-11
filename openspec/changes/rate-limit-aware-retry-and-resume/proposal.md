# Rate-limit-aware retry metadata and automatic resume

## Why

When every Anthropic account is exhausted, Claude Code receives a generic 503 and the
turn simply dies; scheduled (headless) runs stay dead until a human types "continue".
The proxy already knows the earliest account reset time but does not expose it on the
wire, and the launcher has no way to wait for or resume an interrupted run.

## What Changes

- `/v1/messages` account-exhaustion failures return Anthropic-native `429` /
  `rate_limit_error` envelopes with `retry-after` and
  `anthropic-ratelimit-unified-reset` headers when the earliest reset is known.
- Mid-stream failures (selection retries exhausted after the response has started)
  emit a structured error instead of silently truncating the stream: an SSE
  `event: error` block for streaming requests, a JSON error envelope for
  non-streaming requests.
- `/api/anthropic/session-route` error envelopes gain `error.retryAt` (RFC 3339 UTC)
  and `error.retryAfterSeconds`, plus a `retry-after` response header, when the
  earliest reset is known.
- Upstream reset-header parsing accepts both RFC 3339 and Unix-epoch
  (seconds or milliseconds) values.
- The Claude launcher (`clients/claude-lb-launch`) waits at preflight until the
  advertised reset before launching (bounded, interruptible, configurable via
  `CLAUDE_LB_WAIT_FOR_LIMIT` / `CLAUDE_LB_WAIT_MAX_SECONDS`), and for headless
  (`-p`) runs auto-resumes an interrupted session with
  `claude --resume <session-id>` after limits reset
  (`CLAUDE_LB_AUTO_RESUME`, bounded attempts).

## Impact

- Affected specs: `account-routing`
- Affected code: `app/modules/proxy/anthropic_service.py`,
  `app/modules/proxy/api.py`, `clients/claude-lb-launch`,
  `tests/integration/test_anthropic_proxy.py`, `tests/unit/test_claude_lb_launch.py`
- Claude Code shows its native rate-limit UX (or rides out short waits via its own
  retry loop) instead of a generic API error; scheduled runs self-heal after resets.
- Local-overload 429s (proxy admission control) are unchanged and remain distinct
  from upstream-quota 429s per `proxy-admission-control`.
