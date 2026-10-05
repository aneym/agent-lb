## Why
Retired glossary terms need visible, non-blocking reports before a separate rename can be reviewed. Glossary entries also need a completeness check.

## What Changes
- Add `of words check`, `of words rename --plan`, and `of words status` with JSON and text reports.
- Add `of glossary check` for complete glossary entries in CONTEXT.md or an explicit file.
- Define version-1 terms files and default glossary paths. No command changes repository contents.

## Capabilities
### New Capabilities
- `open-factory-words`: read-only retired-word reports and glossary completeness checks.

### Modified Capabilities
None.

## Impact
Open Factory CLI, terms schema and offline CLI integration tests. No routing, service, credentials or enforcement-policy changes. Validation check: run `python3 -m pytest -q tests/words` in clients/open-factory on ax42.
