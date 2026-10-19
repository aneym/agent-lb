---
id: module-support-pools
product: agent-lb
owner: weekend-finish
status: live
covers: []
paths: ["app/modules/pools/", "features/agent-lb/module-support-pools.md"]
proofs: []
unmapped:
  - {"path":"app/modules/pools/seat_schemas.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/pools/service.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/pools/plan.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/pools/seat_quota.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/pools/__init__.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/pools/seat_budget.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/pools/schemas.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/pools/reservations.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/pools/seat_quota_scheduler.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/pools/cli_seats.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
---
# Pools support

## Sub-features
Internal service, schema, repository, or scheduler modules in this route group.

## How to get to it
Use the public HTTP route feature for this group.

## Driving it
These modules support the route feature and are not directly invoked.

## Gotchas
Internal files may be shared across route handlers.
