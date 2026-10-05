## Why

On 2026-10-05 two Claude Code calls from one tab waited 9 and 13 minutes while writing under 310 tokens each. Request `f7612404-0917-4bce-af5a-69281bea4432` (account `502ebd48`, 135k context) got its first token 553.6 s after it was sent and then streamed all 146 output tokens in 0.85 s. The 13-minute stall has no request log row for its first attempt; its timing fits a call Claude Code abandoned at its own 600 s timeout and retried (the retry, `459d27b2-2cbc-4c42-9afb-606589365599`, took 145.7 s). No throttle, adaptive cap or admission queue was active. From 13:00 to 16:00 UTC first-token times rose 5 to 7 times for both providers and every account, more for larger requests, with connect timeouts, broken pipes and resets on Anthropic connections. The OpenAI path, which has a 60 s first-event bound with failover, kept every call under 120 s. The Anthropic path had no bound short of the 600 s total budget, so 88 Anthropic calls waited more than 180 s and 32 waited more than 300 s.

## What Changes

- A streamed `/v1/messages` attempt that receives no response body bytes within `anthropic_first_byte_timeout_seconds` (default 180 s, counted from send) is closed, logged as `upstream_first_byte_timeout`, counted as a transient account error, and moved to another account at once. Nothing has reached the client, so the failover is invisible to it. When every candidate stalls, the client gets a retryable `overloaded_error`.
- A stream that goes silent for `anthropic_stream_idle_timeout_seconds` (default 300 s) after bytes went out ends with an `overloaded_error` SSE event, logged as `upstream_stream_idle_timeout`, instead of hanging until the total budget.
- The connect-retry helper no longer retries a header wait that ran past the first-byte bound on the same account.
- Non-streamed calls are not bounded this way, because a non-streamed reply legitimately sends nothing until the message is done.

## Capabilities

### Modified Capabilities

- `anthropic-messages-compat`: streamed calls have a first-byte bound with failover and a stream-idle bound.

## Impact

- `app/modules/proxy/anthropic_service.py`, `app/core/config/settings.py`.
- The normal-day maximum first-token time was 75 s (2026-10-03 and 2026-10-04), so the 180 s default should not fire outside a degraded upstream path. A failover lands on an account without the prompt cache, so it costs a cache write; that is the trade for not waiting minutes.
