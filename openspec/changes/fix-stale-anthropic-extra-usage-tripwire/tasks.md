## 1. Selection

- [x] 1.1 Drop active extra-usage tripwire cooldowns for accounts whose latest primary snapshot shows headroom in `_provider_quota_eligibility`.
- [x] 1.2 Keep the tripwire blocking for exhausted or unknown primary state.

## 2. Verification

- [x] 2.1 Acceptance tests: a stale tripwire on a recovered account is selected; a tripwire on an exhausted account stays blocked.
- [x] 2.2 `ruff check app` and strict OpenSpec validation.
