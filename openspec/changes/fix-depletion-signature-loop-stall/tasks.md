# Tasks

## 1. Fix

- [x] 1.1 Replace the per-row blake2b/`repr()` history digest with the
      edges-only signature in `filter_depletion_history_since` and
      `attach_depletion_history_signature`.
- [x] 1.2 Update #588 regression tests to the edges-only contract
      (edge-visible corrections invalidate; interior-only mutation
      limitation documented in the test docstring).

## 2. Validation

- [x] 2.1 `uv run ruff check` clean; full `tests/unit` suite green.
- [x] 2.2 Deploy to studio and confirm health flapping stops (watchdog log
      quiet, /health p99 < 1s under dashboard polling).
