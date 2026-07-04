# Tasks

- [x] Parse `limits[]` in `AnthropicOAuthUsagePayload` and surface the
      Fable-scoped weekly entry (`app/core/clients/anthropic_usage.py`,
      `app/core/usage/models.py` as needed).
- [x] Persist the scoped entry as additional usage
      (`anthropic_fable_scoped_weekly`) on every usage refresh
      (`app/modules/usage/updater.py` or the equivalent ingest site).
- [x] Setting `anthropic_fable_scoped_max_used_percent` (default 100.0)
      (`app/core/config/settings.py`).
- [x] Eligibility + burn set prefer fresh scoped data (recorded ≤6h) over the
      overall-weekly heuristic (`app/modules/proxy/anthropic_service.py`).
- [x] `fableEligible` mapper reflects fresh scoped data when present
      (`app/modules/accounts/mappers.py`).
- [x] `fableEligible` returns false for Anthropic rows outside the routable
      pool (reauth-required, deactivated, paused, or subscription-canceled),
      even if stale/fresh usage data has Fable headroom.
- [x] Tests: payload parsing (scoped entry, absent limits, non-Fable scopes
      ignored); persistence on refresh; eligibility via scoped signal both
      directions (scoped-hot/overall-cool excluded, scoped-cool/overall-hot
      admitted); burn set via scoped signal; heuristic fallback when scoped
      data absent/stale; mapper flag; non-routable rows suppressed.
- [x] Validate: pytest, ruff, `openspec validate`, service restart + live
      check that scoped rows appear and eligibility matches upstream percents.
