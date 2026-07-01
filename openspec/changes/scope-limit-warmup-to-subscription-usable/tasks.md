# Tasks

- [x] Gate `_account_is_safe_candidate` on `is_subscription_usable` in
      `app/modules/limit_warmup/service.py`.
- [x] Add regression test `test_subscription_canceled_account_is_skipped` in
      `tests/unit/test_limit_warmup.py` (no sends, no attempts, no request logs).
- [x] Update `usage-refresh-policy` delta spec (unsafe-account-states scenario covers
      subscription-canceled accounts).
- [x] Validate: `uv run pytest tests/unit/test_limit_warmup.py`, `ruff check`,
      `openspec validate --specs`.
