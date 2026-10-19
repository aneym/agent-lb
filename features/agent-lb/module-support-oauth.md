---
id: module-support-oauth
product: agent-lb
owner: weekend-finish
status: live
covers: []
paths: ["app/modules/oauth/", "features/agent-lb/module-support-oauth.md"]
proofs: []
unmapped:
  - {"path":"app/modules/oauth/service.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/oauth/token_account.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/oauth/__init__.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/oauth/schemas.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/oauth/callbacks.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/oauth/state.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
---
# Oauth support

## Sub-features
Internal service, schema, repository, or scheduler modules in this route group.

## How to get to it
Use the public HTTP route feature for this group.

## Driving it
These modules support the route feature and are not directly invoked.

## Gotchas
Internal files may be shared across route handlers.
