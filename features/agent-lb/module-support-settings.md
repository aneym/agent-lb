---
id: module-support-settings
product: agent-lb
owner: weekend-finish
status: live
covers: []
paths: ["app/modules/settings/", "features/agent-lb/module-support-settings.md"]
proofs: []
unmapped:
  - {"path":"app/modules/settings/service.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/settings/__init__.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/settings/schemas.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/settings/repository.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/settings/upstream_proxy_api.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
---
# Settings support

## Sub-features
Internal service, schema, repository, or scheduler modules in this route group.

## How to get to it
Use the public HTTP route feature for this group.

## Driving it
These modules support the route feature and are not directly invoked.

## Gotchas
Internal files may be shared across route handlers.
