---
id: module-support-api-keys
product: agent-lb
owner: weekend-finish
status: live
covers: []
paths: ["app/modules/api_keys/", "features/agent-lb/module-support-api-keys.md"]
proofs: []
unmapped:
  - {"path":"app/modules/api_keys/reset_scheduler.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/api_keys/service.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/api_keys/repository_data.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/api_keys/reservation_pricing.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/api_keys/usage_calculations.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/api_keys/__init__.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/api_keys/key_validation.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/api_keys/schemas.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/api_keys/repository_protocol.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/api_keys/reservation_repository.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/api_keys/repository.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/api_keys/usage_service.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/api_keys/usage_repository.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/api_keys/service_data.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/api_keys/limit_windows.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/api_keys/reservation_service.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
---
# Api Keys support

## Sub-features
Internal service, schema, repository, or scheduler modules in this route group.

## How to get to it
Use the public HTTP route feature for this group.

## Driving it
These modules support the route feature and are not directly invoked.

## Gotchas
Internal files may be shared across route handlers.
