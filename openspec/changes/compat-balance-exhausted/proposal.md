## Why
GLM and Kimi balance failures currently re-arm temporary cooldowns even though only an operator restoring balance can recover the account.

## What Changes
- Classify GLM code 1113 and Kimi suspended/insufficient-balance errors before generic upstream handling.
- Reuse the existing paused state with `balance_exhausted: <upstream code>` and the existing reactivate operation; do not record a rolling cooldown.
- Report no balance in subsequent proxy errors, account status, and pool presentation.

## Impact
No schema or credential changes. Only recognized GLM/Kimi balance errors park an account; unrelated rate limits retain their existing handling.
