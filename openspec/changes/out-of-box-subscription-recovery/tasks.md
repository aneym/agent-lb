# Tasks

- [x] Add `account_pulse_recovery_interval_seconds` setting (default 900, gt=0)
      next to the other `account_pulse_*` settings.
- [x] Add the recovery lane to `AccountPulseScheduler`: per-wake full/recovery
      decision in `_run_loop`, `recovery_pulse_once`, recovery-pending
      candidate filter, and a recovery-capped failure backoff.
- [x] Wire the setting through `build_account_pulse_scheduler`.
- [x] Unit tests (`tests/unit/test_account_pulse.py`): recovery pass restores a
      canceled ledger and reactivates a reauth-required account, skips healthy
      and paused accounts, backoff capped at the recovery interval, run-loop
      fast cadence, full-pass behavior unchanged.
- [x] OpenSpec delta (`usage-refresh-policy`).
- [x] Validate: `uv run ruff check app tests`,
      `uv run pytest tests/unit/test_account_pulse.py -q`.
