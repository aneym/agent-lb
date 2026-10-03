## Why

Anthropic accounts with a spent weekly window and a fresh five-hour window stayed eligible, and a 200 that billed extra usage was still streamed to the client. Usage refreshes then cleared the tripwire and the next request billed again.

## What Changes

- When `anthropic_route_to_extra_usage` is false, a latest secondary snapshot at or above 100% with a future or unknown reset is ineligible for `/v1/messages`, and its reset is included in the pool-exhausted retry time.
- The extra-usage tripwire lifts only when both a primary and a secondary snapshot recorded after it are under 100%. An account with no secondary snapshot still lifts on primary headroom. The tripwire's own reset still ends it.
- A 2xx response that trips the extra-usage headers is not forwarded. The cooldown is recorded, the upstream body is closed unread, a request log row is stored with `extra_usage_refused`, and selection continues. Paid-fallback accounts keep the previous stream-through behavior.

## Capabilities

### New Capabilities

- None

### Modified Capabilities

- `account-routing`: subscription routing must not serve Anthropic `/v1/messages` from paid extra usage while the extra-usage opt-in is false.

## Impact

- Code: `app/modules/proxy/anthropic_service.py`
- Tests: Anthropic `/v1/messages` eligibility and extra-usage failover
