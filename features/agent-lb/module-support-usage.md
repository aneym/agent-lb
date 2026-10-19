---
id: module-support-usage
product: agent-lb
owner: weekend-finish
status: live
covers: []
paths: ["app/modules/usage/", "features/agent-lb/module-support-usage.md"]
proofs: []
unmapped:
  - {"path":"app/modules/usage/builders.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/usage/service.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/usage/identity_mismatch.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/usage/depletion_service.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/usage/additional_usage_reads.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/usage/__init__.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/usage/updater.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/usage/sqlite_history.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/usage/schemas.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/usage/mappers.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/usage/repository.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/usage/history_trends.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/usage/additional_quota_keys.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
---
# Usage support

## Sub-features
Internal service, schema, repository, or scheduler modules in this route group.

## How to get to it
Use the public HTTP route feature for this group.

## Driving it
These modules support the route feature and are not directly invoked.

## Gotchas
Internal files may be shared across route handlers.
