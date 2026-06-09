## Why

The public LLM-usage feed powers the website's tokenmaxxing dashboard and the
GitHub-backed aggregate snapshots. The current public usage window returns one
extra daily bucket: `days=7` yields 8 rows and `days=365` yields 366 rows. That
confuses dashboard ranges, makes backfilled snapshots disagree with their
declared `period.days`, and hides publisher freshness problems behind a payload
that is technically live but not contract-clean.

## What Changes

- Define the public usage window as exactly `days` contiguous UTC date buckets,
  inclusive of the current UTC day and starting at `end - (days - 1)`.
- Keep the request-log filter aligned to that same start boundary.
- Pin integration coverage for exact bucket counts and oldest/newest dates.
- Treat the GitHub-published `usage-*.json` snapshots as the intended backfill
  artifacts consumed by the website.

## Impact

- Code: `app/modules/public_usage/service.py`
- Tests: `tests/integration/test_public_usage.py`
- Specs: `openspec/changes/fix-public-usage-window-backfill/specs/public-usage/spec.md`
