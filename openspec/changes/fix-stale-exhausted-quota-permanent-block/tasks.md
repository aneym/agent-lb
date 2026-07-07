# Tasks

- [x] Add `_bounded_exhaustion_reset_at` helper in the usage updater that
      synthesizes a bounded reset for exhausted windows omitting upstream reset
      info (`now + limit_window_seconds`, else `now + 3600`).
- [x] Apply the bounded-reset synthesis to the Anthropic additional-quota merge
      candidate and the `primary` usage window write; leave `secondary` and
      `monthly` writes unchanged to preserve monthly-cap semantics.
- [x] Read-time: `_anthropic_cooldown_is_active` treats an exhausted row with
      `reset_at = None` as not active (re-admit).
- [x] Read-time: the primary-window exhaustion prefilter excludes only
      exhausted rows with a bounded, still-future `reset_at`; `None`-reset rows
      re-admit the account.
- [x] Read-time: `_additional_quota_window` renders an exhausted `None`-reset row
      as re-admitted (`used_percent = 0`, `reset_at = None`).
- [x] Regression: additional-quota row with `used_percent = 100`, `reset_at =
      None` keeps the account selectable via `_provider_quota_eligibility`.
- [x] Regression: primary usage window with `used_percent = 100`, `reset_at =
      None` keeps the account selectable via `_provider_quota_eligibility`.
- [x] Regression: bounded still-future exhaustion continues to block and reports
      reset metadata.
- [x] Regression: updater merge synthesizes a bounded `reset_at` for an
      exhausted window when upstream omits reset info (window-length and default
      horizon cases); non-exhausted window stays `None`.
- [x] Regression: dashboard additional-quota window mapping does not display a
      permanent 100% for a `None`-reset row.
- [x] Verify existing monthly-window tests stay green.
- [x] Run focused tests (`tests/unit/test_usage_updater.py`,
      `tests/unit/test_load_balancer.py`,
      `tests/unit/test_additional_usage_service.py`) and `ruff check app clients`.
- [x] Validate OpenSpec change locally
      (`npx --yes @fission-ai/openspec@latest validate --specs`).
