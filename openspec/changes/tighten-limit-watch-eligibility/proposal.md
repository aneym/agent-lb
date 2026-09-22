# Proposal

## Why

The coding-agent seat guard depends on limit-watch telemetry, but the poller currently treats missing quota windows as usable. That can admit Anthropic seats without verified current headroom.

## What Changes

- Make limit-watch require numeric, above-reserve core windows for Anthropic accounts.
- Preserve the documented OpenAI exception: a null primary window is valid when the weekly window has headroom.
- Require Fable-scoped weekly headroom for the Fable eligible count and reject only stale Fable-scoped telemetry; `lastRefreshAt` is an auth-token refresh timestamp, not telemetry.
- Add provider minima and privacy-safe unusable-account reasons to the snapshot.

## Capabilities

### New Capabilities
- `coding-agent-capacity-guard`: Fail-closed telemetry contract for coding-agent seat admission.

### Modified Capabilities
- None.

## Impact

- `config/coding-agents/limit-watch` snapshot production
- `config/coding-agents/seat-guard.py` compatible snapshot consumption
- Offline stdlib regression tests
