---
id: module-support-account-schedule
product: agent-lb
owner: weekend-finish
status: live
covers: []
paths: ["app/modules/account_schedule/", "features/agent-lb/module-support-account-schedule.md"]
proofs: []
unmapped:
  - {"path":"app/modules/account_schedule/service.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/account_schedule/__init__.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/account_schedule/schemas.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/account_schedule/scheduler.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/account_schedule/repository.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
---
# Account Schedule support

## Sub-features
Internal service, schema, repository, or scheduler modules in this route group.

## How to get to it
Use the public HTTP route feature for this group.

## Driving it
These modules support the route feature and are not directly invoked.

## Gotchas
Internal files may be shared across route handlers.
