---
id: module-support-sessions
product: agent-lb
owner: weekend-finish
status: live
covers: []
paths: ["app/modules/sessions/", "features/agent-lb/module-support-sessions.md"]
proofs: []
unmapped:
  - {"path":"app/modules/sessions/service.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/sessions/__init__.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/sessions/schemas.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/sessions/mappers.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/sessions/repository.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
---
# Sessions support

## Sub-features
Internal service, schema, repository, or scheduler modules in this route group.

## How to get to it
Use the public HTTP route feature for this group.

## Driving it
These modules support the route feature and are not directly invoked.

## Gotchas
Internal files may be shared across route handlers.
