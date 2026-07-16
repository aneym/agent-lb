# Tasks

## 1. Routing behavior

- [x] 1.1 Add the transient-cooldown horizon constant and pool-wide readmission branch to `_provider_quota_eligibility`.
- [x] 1.2 Keep primary/secondary exhaustion, extra-usage tripwire, unbounded resets, and beyond-horizon resets blocking.

## 2. Regression coverage

- [x] 2.1 Pool-wide near-reset cooldowns re-admit all transient candidates.
- [x] 2.2 A healthy candidate keeps cooled accounts excluded (bypass is last-resort only).
- [x] 2.3 Primary-window exhaustion keeps an account blocked despite a near-reset requested-quota cooldown.

## 3. Validation and rollout

- [x] 3.1 `ruff check app clients`, unit + integration Anthropic proxy suites, strict OpenSpec validation.
- [x] 3.2 Restart `com.aneyman.agent-lb` and exercise a real Anthropic request via `http://127.0.0.1:2455`.
