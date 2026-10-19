---
id: module-support-federation
product: agent-lb
owner: weekend-finish
status: live
covers: []
paths: ["app/modules/federation/", "features/agent-lb/module-support-federation.md"]
proofs: []
unmapped:
  - {"path":"app/modules/federation/service.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/federation/peer_client.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/federation/repository_transfers.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/federation/push_auth.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/federation/__init__.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/federation/repository_usage.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/federation/schemas.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/federation/exceptions.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/federation/scheduler.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/federation/repository.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/federation/push_receiver.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/federation/push.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
---
# Federation support

## Sub-features
Internal service, schema, repository, or scheduler modules in this route group.

## How to get to it
Use the public HTTP route feature for this group.

## Driving it
These modules support the route feature and are not directly invoked.

## Gotchas
Internal files may be shared across route handlers.
