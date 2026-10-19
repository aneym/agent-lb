---
id: module-support-routing-view
product: agent-lb
owner: weekend-finish
status: live
covers: []
paths: ["app/modules/routing_view/", "features/agent-lb/module-support-routing-view.md"]
proofs: []
unmapped:
  - {"path":"app/modules/routing_view/decisions.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/routing_view/__init__.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/routing_view/menu.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/routing_view/schemas.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
---
# Routing View support

## Sub-features
Internal service, schema, repository, or scheduler modules in this route group.

## How to get to it
Use the public HTTP route feature for this group.

## Driving it
These modules support the route feature and are not directly invoked.

## Gotchas
Internal files may be shared across route handlers.
