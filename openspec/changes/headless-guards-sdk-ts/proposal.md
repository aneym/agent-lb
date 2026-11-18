## Why

The Rails host user agent is `claude-cli/2.1.288 (external, sdk-ts, agent-sdk/0.3.288)`.
The untagged headless Opus warning and upload classification only looked for `sdk-cli`,
so that client skipped both the untagged-Opus warning and the batch upload class.

## What Changes

- Treat user agents containing `sdk-cli`, `sdk-ts`, or `agent-sdk/` as headless Claude.
- Keep `workload/` as a batch upload class. An explicit `x-agent-lb-priority` of
  `high` or `batch` still wins.
- Serve untagged headless Opus from those user agents and log the warning
  `untagged_headless_opus`. A seat, or priority `high`, is exempt from the warning.
  The refusal and its `refuse_untagged_headless_opus` setting are deleted
  (red-tape cut, 2026-11-18).

## Capabilities

### New Capabilities

- None

### Modified Capabilities

- `anthropic-messages-compat`: the untagged headless Opus warning covers sdk-ts and
  agent-sdk, and priority `high` is exempt.

## Impact

- Code: `app/core/upload_admission.py`, `app/modules/proxy/api.py`
- Tests: headless Opus scenarios, upload admission
- `refuse_untagged_headless_opus` is deleted
