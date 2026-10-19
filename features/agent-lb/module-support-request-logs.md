---
id: module-support-request-logs
product: agent-lb
owner: weekend-finish
status: live
covers: []
paths: ["app/modules/request_logs/", "features/agent-lb/module-support-request-logs.md"]
proofs: []
unmapped:
  - {"path":"app/modules/request_logs/service.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/request_logs/__init__.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/request_logs/schemas.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/request_logs/mappers.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/request_logs/aggregates.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/request_logs/edge_source.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/request_logs/queries.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/request_logs/repository.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
---
# Request Logs support

## Sub-features
Internal service, schema, repository, or scheduler modules in this route group.

## How to get to it
Use the public HTTP route feature for this group.

## Driving it
These modules support the route feature and are not directly invoked.

## Gotchas
Internal files may be shared across route handlers.
