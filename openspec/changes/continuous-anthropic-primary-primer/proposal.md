# Continuous Anthropic primary-window primer

## Why

The existing Anthropic primer is scheduled through the optional limit-warmup path. It is enabled on the live dashboard, but its HOLD trigger requires an exhausted previous sample and its planner restricts sends to working hours. A partially used five-hour window that resets outside that trigger is not primed.

## What changes

- Reuse the existing Anthropic Messages primer and durable limit-warmup attempts to send one minimal Haiku request after each known primary reset.
- Require fresh primary and weekly usage showing free quota before sending, then refresh usage again to confirm a new primary reset.
- Retry explicit failures with backoff; retain uncertain pending and unconfirmed sends instead of risking a duplicate.
- Expose the last confirmed prime time in account status and the `agent-lb status` CLI.

## Impact

`usage-refresh-policy` gains Anthropic-specific continuous priming. The old optional OpenAI warmup policy remains unchanged.
