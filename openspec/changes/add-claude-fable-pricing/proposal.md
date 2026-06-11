# add-claude-fable-pricing

## Why

Claude Fable 5 request logs are currently missing from dashboard and API-key cost rollups because the Anthropic pricing registry does not recognize the `claude-fable-5` model family. Those rows persist `cost_usd = null`, so reports understate Claude spend until pricing resolution and historical rows are corrected.

## What Changes

- Add official Claude Fable 5 pricing to the Anthropic pricing registry.
- Add Claude Mythos 5 pricing while touching the same official price table, because Anthropic documents it with the same rates and alias shape.
- Backfill existing Fable request-log costs from persisted token counts using the existing Anthropic 5-minute cache-write default.
- Document that new model pricing updates must be verified from a fresh source, preferably official provider docs, before code and backfill work.

## Impact

- Affected specs: `api-keys`
- Affected code: `app/core/anthropic/pricing.py`, `tests/unit/test_anthropic_core.py`
- Data repair: live `request_logs.cost_usd` rows for `claude-fable-5*`
