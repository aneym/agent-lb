---
id: module-support-dashboard
product: agent-lb
owner: weekend-finish
status: live
covers: []
paths: ["app/modules/dashboard/", "features/agent-lb/module-support-dashboard.md"]
proofs: []
unmapped:
  - {"path":"app/modules/dashboard/builders.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/dashboard/service.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/dashboard/__init__.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/dashboard/schemas.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/dashboard/weekly_pace.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/dashboard/repository.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
---
# Dashboard support

## Sub-features
Internal service, schema, repository, or scheduler modules in this route group.

## How to get to it
Use the public HTTP route feature for this group.

## Driving it
These modules support the route feature and are not directly invoked.

## Gotchas
Internal files may be shared across route handlers.
