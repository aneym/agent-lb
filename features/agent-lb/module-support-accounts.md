---
id: module-support-accounts
product: agent-lb
owner: weekend-finish
status: live
covers: []
paths: ["app/modules/accounts/", "features/agent-lb/module-support-accounts.md"]
proofs: []
unmapped:
  - {"path":"app/modules/accounts/pulse.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/accounts/service.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/accounts/identity_repository.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/accounts/summary_state.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/accounts/claude_reset_policy.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/accounts/reset_credit_cache.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/accounts/read_cache.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/accounts/reset_credit_scheduler.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/accounts/reset_credit_attempts.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/accounts/__init__.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/accounts/state_repository.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/accounts/subscription_status.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/accounts/schemas.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/accounts/mappers.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/accounts/pulse_verdicts.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/accounts/probes.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/accounts/repository.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/accounts/reset_credit_recovery.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/accounts/credits.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/accounts/cache_warmer.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/accounts/usage_repository.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/accounts/auth_manager.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/accounts/pulse_backend.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
---
# Accounts support

## Sub-features
Internal service, schema, repository, or scheduler modules in this route group.

## How to get to it
Use the public HTTP route feature for this group.

## Driving it
These modules support the route feature and are not directly invoked.

## Gotchas
Internal files may be shared across route handlers.
