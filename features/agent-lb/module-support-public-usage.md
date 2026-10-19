---
id: module-support-public-usage
product: agent-lb
owner: weekend-finish
status: live
covers: []
paths: ["app/modules/public_usage/", "features/agent-lb/module-support-public-usage.md"]
proofs: []
unmapped:
  - {"path":"app/modules/public_usage/service.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/public_usage/__init__.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/public_usage/schemas.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
---
# Public Usage support

## Sub-features
Internal service, schema, repository, or scheduler modules in this route group.

## How to get to it
Use the public HTTP route feature for this group.

## Driving it
These modules support the route feature and are not directly invoked.

## Gotchas
Internal files may be shared across route handlers.
