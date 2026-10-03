## Why

The Rails host user agent is `claude-cli/2.1.288 (external, sdk-ts, agent-sdk/0.3.288)`.
Headless Opus refusal and upload classification only looked for `sdk-cli`, so that
client skipped both the untagged-Opus guard and the batch upload class.

## What Changes

- Treat user agents containing `sdk-cli`, `sdk-ts`, or `agent-sdk/` as headless Claude.
- Keep `workload/` as a batch upload class. An explicit `x-agent-lb-priority` of
  `high` or `batch` still wins.
- Refuse untagged headless Opus for those user agents when
  `refuse_untagged_headless_opus` is on. A seat, or priority `high`, is exempt.
  The flag default stays off. While it is off, the would-refuse log still fires
  for an untagged headless Opus request and does not fire when priority is `high`.

## Capabilities

### New Capabilities

- None

### Modified Capabilities

- `anthropic-messages-compat`: untagged headless Opus refusal covers sdk-ts and
  agent-sdk, and priority `high` is exempt.

## Impact

- Code: `app/core/upload_admission.py`, `app/modules/proxy/api.py`
- Tests: headless Opus scenarios, upload admission
- `refuse_untagged_headless_opus` stays `False`
