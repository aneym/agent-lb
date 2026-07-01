# Tasks

- [x] Settings: `anthropic_fable_routing_enabled`,
      `anthropic_fable_weekly_max_used_percent` (`app/core/config/settings.py`).
- [x] `_is_fable_model` helper + weekly-usage read and Fable/non-Fable pool
      shaping in `_provider_quota_eligibility`; plumb `model` through
      `_select_account` and the session-route retry path
      (`app/modules/proxy/anthropic_service.py`).
- [x] `burn_first_account_ids` param on `LoadBalancer.select_account`, applied
      in `_build_states` without touching stored policies other than `normal`
      (`app/modules/proxy/load_balancer.py`).
- [x] `fableEligible` on `AccountSummary` (`schemas.py`, `mappers.py`).
- [x] Tests: eligibility filtering + fallback + burn-set computation, burn
      stamp tiering (incl. preserve untouched), mapper flag, fable model
      classification.
- [x] Regression fix: snapshot weekly usage scalars before leaving the repository
      context so live Anthropic selection does not touch detached ORM rows.
- [x] Validate: pytest (load balancer, anthropic service/proxy, mappers), ruff,
      `openspec validate --specs`, service restart + live routing log check.
