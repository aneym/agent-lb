## 1. Public Usage Contract

- [x] 1.1 Add OpenSpec delta for exact `days` bucket behavior and GitHub snapshot backfill artifacts.
- [x] 1.2 Change `build_public_usage()` to return exactly `days` contiguous daily/trend rows.
- [x] 1.3 Add regression coverage for exact bucket count and date boundaries.
- [x] 1.4 Verify public usage tests pass.
- [x] 1.5 Re-run the Studio publisher so public GitHub snapshots reflect the corrected/fresh payload.

Verification:

- `uv run pytest tests/integration/test_public_usage.py` passed on 2026-05-30.
- Studio canonical service was restarted on 2026-05-30; `GET /api/usage/public?days=7` returned exactly 7 rows from 2026-05-24 through 2026-05-30.
- Studio publisher ran successfully on 2026-05-30 and pushed fresh public snapshots through commit `3816639`.
- `openspec validate --specs` could not run because `openspec` is not installed on this shell PATH or in `uv run`.
