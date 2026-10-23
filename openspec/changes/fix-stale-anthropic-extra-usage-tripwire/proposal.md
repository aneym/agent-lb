## Why
A persisted Anthropic extra-usage tripwire can outlive subscription exhaustion and exclude a recovered account, delaying Claude Code startup and overloading the remaining pool.

## What Changes
- Retain fresh extra-usage tripwires until a newer primary snapshot confirms recovery; retain the gate during active primary-window exhaustion.
- Use that same rule for ordinary eligibility and transient request-cooldown recovery.
- Preserve request quotas, secondary-window gates, and explicit paid-fallback policy.

## Capabilities

### New Capabilities
None.

### Modified Capabilities
- `account-routing`: Clear historical extra-usage tripwires only after newer primary usage confirms recovery.

## Impact
Changes Anthropic quota eligibility in `app/modules/proxy/anthropic_service.py`. No persistence or API schema changes.
