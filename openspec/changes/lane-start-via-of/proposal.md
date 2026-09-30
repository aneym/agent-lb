# Lane tab stand-in rollout

## Why
Interactive herdr tabs do not currently identify their intent, leaving the stand-in policy dormant. A table-controlled canary enables rollout without changing request payloads.

## What Changes
- Add an off/canary/all rollout policy, initially allowing only tab `w5H:tC8`.
- Resolve launcher intent and lane from the installed policy and herdr kind registry, with explicit intent taking precedence.
- Keep print runs and child launchers unchanged; fail open within a 50 ms lookup budget.
- Include resolved tags and the resolution reason in dry-run output.

## Impact
Affected specification: lane-tab-launch. Affected code: clients/claude-lb-launch and config/coding-agents/routing-table.json. No server policy, payload, credential, or installation changes.
