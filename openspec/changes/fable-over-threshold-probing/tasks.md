# Tasks

- [x] Settings: `anthropic_fable_over_threshold_probe_enabled` (default true),
      `anthropic_fable_probe_ttl_seconds` (default 43200)
      (`app/core/config/settings.py`).
- [x] Pulse: Fable probe for routable Anthropic accounts at/over the weekly
      threshold; classify 2xx / refusal / inconclusive; write the
      `anthropic_fable_access` marker; never touch account status
      (`app/modules/accounts/pulse.py` + probe service reuse).
- [x] Eligibility: over-threshold accounts with a fresh capable marker join the
      Fable pool in `_provider_quota_eligibility`
      (`app/modules/proxy/anthropic_service.py`).
- [x] Tests: capable marker admits an over-threshold account to Fable routing;
      refused/stale/absent markers keep it excluded; marker TTL expiry;
      refusal standing until weekly reset; probe outcome does not alter
      account status; disabled flag skips probing.
- [x] Validate: pytest, ruff, `openspec validate`, service restart + live check.
