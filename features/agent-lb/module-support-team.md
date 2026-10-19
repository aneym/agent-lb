---
id: module-support-team
product: agent-lb
owner: weekend-finish
status: live
covers: []
paths: ["app/modules/team/", "features/agent-lb/module-support-team.md"]
proofs: []
unmapped:
  - {"path":"app/modules/team/service.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/team/pool_share.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/team/__init__.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/team/member_policy.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/team/schemas.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/team/gate.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/team/windows.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/team/repository.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
---
# Team support

## Sub-features
Internal service, schema, repository, or scheduler modules in this route group.

## How to get to it
Use the public HTTP route feature for this group.

## Driving it
These modules support the route feature and are not directly invoked.

## Gotchas
Internal files may be shared across route handlers.
