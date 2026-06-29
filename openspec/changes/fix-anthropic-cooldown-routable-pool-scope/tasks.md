# Tasks

- [x] Promote `selectable_accounts` to a public helper in the proxy load
      balancer as the single source of truth for the routable pool.
- [x] Scope `_provider_quota_eligibility` to the routable pool so unusable rows
      are neither counted as blocked nor passed to the selector as candidates.
- [x] Scope `_selection_failure_details` headline counts and status summary to
      the routable pool and label stored-but-unusable rows separately.
- [x] Add regression: all routable accounts cooling down (with canceled and
      deactivated rows present) returns a native cooldown `429` with retry
      metadata, not a `503`.
- [x] Add regression: selection-failure diagnostics count only routable
      accounts and label stored-but-unusable rows.
- [x] Update the unit test referencing the renamed helper.
- [x] Run focused tests (`tests/integration/test_anthropic_proxy.py`,
      `tests/unit/test_proxy_load_balancer_refresh.py`, `tests/unit/test_load_balancer.py`).
- [x] Validate OpenSpec change locally (`npx --yes @fission-ai/openspec@latest validate --specs`).
