## 1. Regression Coverage

- [x] 1.1 Add account-pulse tests proving active `anthropic_top` and `anthropic_top_thinking` cooldowns trigger exact quota-shaped probes.
- [x] 1.2 Add tests proving only 2xx clears the probed key while 429, refusal, server, and transport failures preserve cooldown state.
- [x] 1.3 Add a race regression proving a newer concurrent cooldown cannot be overwritten by an older successful probe.

## 2. Cooldown Reconciliation

- [x] 2.1 Extend the Anthropic messages probe helper to support the adaptive-thinking request shape without changing existing callers.
- [x] 2.2 Add injected active-cooldown lookup and atomic compare-and-append cooldown-clear seams to the account pulse.
- [x] 2.3 Reconcile each active Fable model-quota cooldown after the normal account verdict, with exact-key clearing and selection-cache invalidation.

## 3. Validation and Rollout

- [x] 3.1 Run focused pulse/probe tests, ruff, and strict OpenSpec validation.
- [x] 3.2 Restart the live service and prove a stale future cooldown self-heals through the pulse path before a real high-effort `cc` request succeeds.
