# Restore ordinary cooldown fallback

## Why
Error-rule exclusion accidentally removed last-resort selection for ordinary cooldowns.

## What Changes
Track error-rule deadlines separately and restore ordinary cooldown fallback.

## Capabilities
### Modified Capabilities
- `account-routing`: restrict hard cooldown exclusion to error rules.

## Impact
Balancer runtime, selection, error-rule action, and local HTTP proof.
