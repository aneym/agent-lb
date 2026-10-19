---
id: module-support-conversation-archive
product: agent-lb
owner: weekend-finish
status: live
covers: []
paths: ["app/modules/conversation_archive/", "features/agent-lb/module-support-conversation-archive.md"]
proofs: []
unmapped:
  - {"path":"app/modules/conversation_archive/service.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/conversation_archive/__init__.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/conversation_archive/schemas.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
---
# Conversation Archive support

## Sub-features
Internal service, schema, repository, or scheduler modules in this route group.

## How to get to it
Use the public HTTP route feature for this group.

## Driving it
These modules support the route feature and are not directly invoked.

## Gotchas
Internal files may be shared across route handlers.
