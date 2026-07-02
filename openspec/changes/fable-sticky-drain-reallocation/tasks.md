# Tasks

- [x] Setting: `anthropic_fable_sticky_drain_enabled` (`app/core/config/settings.py`).
- [x] `burn_first_sticky_drain` param on `LoadBalancer.select_account` →
      `_select_with_stickiness`; drain-pressure trigger ORed into
      `proactive_reallocate` with guards mirroring `budget_pressured`
      (`app/modules/proxy/load_balancer.py`).
- [x] Pass the flag from `AnthropicProxyService._select_account`
      (`app/modules/proxy/anthropic_service.py`).
- [x] Tests: sticky non-Fable session on an under-threshold account reallocates
      to the over-threshold account and re-pins; disabled flag keeps the pin;
      pinned over-threshold (burn_first) account keeps the pin; Fable-class
      sticky sessions unaffected (`tests/integration/test_anthropic_proxy.py`).
- [x] Validate: pytest (anthropic proxy, load balancer), ruff,
      `openspec validate --specs`, service restart + live selection-log check.
- [x] Fable-scoped session affinity: `_fable` affinity suffix in
      `_messages_affinity_quota_key` and the session-route default/validation
      (`app/modules/proxy/anthropic_service.py`, `app/modules/proxy/api.py`)
      so mixed-model sessions hold two stable pins instead of ping-ponging.
- [x] Tests: mixed-model session keeps both pins across alternation; fast-mode
      Fable affinity; session-route accepts `_fable` affinity keys.
- [x] Validate: pytest, ruff, restart + live re-measure of cache hit rate.
