## 1. Implementation

- [x] 1.1 Gate `/v1/messages` on weekly exhaustion when extra-usage routing is off, and record the weekly reset on the pool-exhausted error.
- [x] 1.2 Lift the extra-usage tripwire only after both windows show headroom recorded later than the tripwire, unless the account has no secondary snapshot.
- [x] 1.3 Refuse a 2xx extra-usage response: record the cooldown, close the body unread, log `extra_usage_refused`, and fail over. Keep streaming when the account is a paid fallback.

## 2. Verification

- [x] 2.1 Add regressions for weekly exhaustion after a fresh primary snapshot, and for an overage 200 that must not be forwarded.
- [x] 2.2 Ran `npx --yes @fission-ai/openspec validate --specs` (40 passed, 0 failed) and the Anthropic proxy check.
