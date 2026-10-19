---
id: module-support-quota-planner
product: agent-lb
owner: weekend-finish
status: live
covers: []
paths: ["app/modules/quota_planner/", "features/agent-lb/module-support-quota-planner.md"]
proofs: []
unmapped:
  - {"path":"app/modules/quota_planner/__init__.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/quota_planner/logic.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/quota_planner/calendar.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/quota_planner/warmup.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/quota_planner/schemas.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/quota_planner/scheduler.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/quota_planner/repository.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
---
# Quota Planner support

## Sub-features
Internal service, schema, repository, or scheduler modules in this route group.

## How to get to it
Use the public HTTP route feature for this group.

## Driving it
These modules support the route feature and are not directly invoked.

## Gotchas
Internal files may be shared across route handlers.
