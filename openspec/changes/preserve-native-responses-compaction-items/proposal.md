## Why

Public Responses normalization drops provider-issued `compaction` output items because they contain opaque encrypted content rather than text. Clients need these checkpoints to persist and replay native server-side compaction.

## What Changes

- Recognize `compaction` as a provider-issued output item and preserve all its fields unchanged.
- Preserve checkpoints in streaming item events, backfilled terminal responses, and collected non-streaming responses.

## Impact

- Affected spec: `responses-api-compat`.
- Affected code: the shared public output-item allowlist in `app/modules/proxy/api.py`.
- Validation: run the supplied acceptance check; no new test is required by the check note.
