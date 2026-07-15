## 1. Routing behavior

- [x] 1.1 Refine Anthropic eligibility so the paid last-resort branch can distinguish a primary-window credit-billing cooldown from cooldowns that must remain blocking.
- [x] 1.2 Re-admit only credit-backed accounts when the opt-in is true and no subscription-covered candidate remains.

## 2. Regression coverage

- [x] 2.1 Add proxy-level tests proving a credit-backed account serves after pool-wide five-hour exhaustion and subscription-covered accounts remain preferred.
- [x] 2.2 Add negative tests proving disabled opt-in, missing credits, and non-primary cooldowns remain blocked.

## 3. Validation and rollout

- [x] 3.1 Run focused Anthropic proxy tests, ruff, application import checks, and strict OpenSpec validation.
- [x] 3.2 Restart `com.aneyman.agent-lb` and exercise the affected local endpoint against `http://127.0.0.1:2455`.
