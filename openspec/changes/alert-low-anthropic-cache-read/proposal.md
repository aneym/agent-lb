## Why

Operators need to see when recent Anthropic traffic is no longer benefiting from prompt-cache reads.

## What Changes

- Add a dashboard-authenticated, read-only aggregate of Anthropic cache token usage over the trailing hour.
- Show a status advisory when cache-read tokens are less than half of observed Anthropic input tokens.
- Preserve an unknown state for no traffic or incomplete token telemetry.

## Impact

- Capability: `advisory-status-cli`.
- Adds one local dashboard API observation; it does not change routing or provider calls.
