# Tasks

- [x] Extract shared probe senders + `classify_probe_result` into
      `app/modules/accounts/probes.py`; delegate the existing service methods.
- [x] Add `AccountPulseScheduler` + `build_account_pulse_scheduler`
      (`app/modules/accounts/pulse.py`) with leader election, jitter, bounded
      concurrency, and per-account failure backoff.
- [x] Add `account_pulse_*` settings (`app/core/config/settings.py`).
- [x] Wire scheduler start/stop into the app lifespan (`app/main.py`).
- [x] Unit tests (`tests/unit/test_account_pulse.py`): classification table, ledger
      restore/cancel, reauth transition, reactivation, paused skip, inconclusive
      no-write + backoff, permanent vs transient refresh failure.
- [x] OpenSpec delta (`usage-refresh-policy`).
- [x] Validate: pytest unit+integration for accounts/warmup/guardian, ruff,
      `openspec validate --specs`, service restart + live verification.
