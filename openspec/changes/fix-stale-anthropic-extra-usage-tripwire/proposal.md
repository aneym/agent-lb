## Why
A persisted Anthropic extra-usage tripwire can outlive subscription exhaustion and exclude a recovered account, delaying Claude Code startup and overloading the remaining pool.

## What Changes
- Apply the extra-usage tripwire only while the account has active primary-window exhaustion.
- Use that same rule for ordinary eligibility and transient request-cooldown recovery.
- Preserve request quotas, secondary-window gates, and explicit paid-fallback policy.

## Capabilities

### New Capabilities
None.

### Modified Capabilities
- `account-routing`: Limit the extra-usage tripwire to active primary-window exhaustion.

## Impact
Changes Anthropic quota eligibility in `app/modules/proxy/anthropic_service.py`. No persistence or API schema changes.
