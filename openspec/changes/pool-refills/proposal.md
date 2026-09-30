## Why

Weekly quota inspection should show when usable spent accounts refill, without counting canceled subscriptions.

## What Changes

- Enrich route pools and menu with UTC reset cohorts and next low-account refill hours.
- Exclude canceled subscriptions, monthly pools and anthropic-fable; unavailable data remains non-blocking.

## Capabilities

### Modified Capabilities

- `coding-agent-routing`

## Impact

`clients/route` and refill inspection tests; no server or credential changes.
