# Tasks

## 1. Fix

- [x] 1.1 Clear stale `deactivation_reason` in the pulse HEALTHY branch for
  `active` accounts, with an audit action
- [x] 1.2 Add regression test
  (`test_pulse_clears_stale_deactivation_reason_on_active_account`)

## 2. Validation

- [x] 2.1 `tests/unit/test_account_pulse.py` passes; ruff clean; strict
  OpenSpec validation passes
- [ ] 2.2 Live: stale reason on alex@kineticapps.io cleared (manual
  `/reactivate` now, pulse covers future occurrences); menubar shows the
  account as healthy
