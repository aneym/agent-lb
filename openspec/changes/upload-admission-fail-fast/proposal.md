## Why

While a gaming hold caps uploads at 200 KB/s, each Anthropic request still uploads its whole body. The admission queue is unbounded, so a waiter that can never fit before the ceiling sits for the full `AGENT_LB_UPLOAD_ADMISSION_MAX_WAIT_SECONDS` (180s) and then returns a 503 that does not say why. On 2026-10-02 that produced 116 `upload_admission_timeout` rows, every one at 180.1s.

## What Changes

- Before queueing, estimate the wait from outstanding and queued bytes. If it already exceeds the ceiling, reject immediately with the rate, backlog, and projected wait.
- Lower the default admission ceiling from 180s to 30s. A waiter that was eligible to queue and still expires keeps the timeout path, with the same numbers plus how long it waited.
- Log one warning for either failure, with the class, rate, backlog, seconds, limit, and a hashed session. Anthropic request logs use `upload_admission_rejected` for the fast path and `upload_admission_timeout` for the waited-out path. HTTP stays 503.
- Leave throttle-off admission, fairness, and the high/batch split unchanged.
- The wait estimate uses the class room `_within_budget` actually admits against: `need = min(nbytes, cap)` with `cap` = 80% of the budget for high and the full budget for batch, and batch counts high outstanding as at most that 80% (batch keeps 20%). `projected = max(0, backlog + need - cap) / rate`.
- `Retry-After` is skipped. `AnthropicProxyError` has no headers; `retry_at` drives the pool-wait path.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `proxy-admission-control`: upload admission fails fast when the projected wait exceeds the ceiling, and the ceiling defaults to 30s.

## Impact

- `app/core/upload_admission.py` and the two Anthropic handlers that persist admission failures.
- Clients see a 503 in well under a second when the queue cannot drain in time, with a message that names the throttle rate and the backlog.
