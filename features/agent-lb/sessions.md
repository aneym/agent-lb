---
id: sessions
product: agent-lb
owner: weekend-finish
status: live
covers: []
paths: ["features/agent-lb/sessions.md"]
proofs: []
unmapped:
  - {"path": "app/modules/sessions/api.py", "date": "2026-10-18", "reason": "dynamic route template /api/sessions/{session_id} is unclassifiable"}
  - {"path": "app/modules/sessions/api.py", "date": "2026-10-18", "reason": "dynamic route template /api/sessions/{session_id}/analytics is unclassifiable"}
  - {"path": "app/modules/sessions/api.py", "date": "2026-10-18", "reason": "dynamic route template /s/{session_id_or_prefix} is unclassifiable"}
---
# Sessions

## Sub-features
HTTP route handlers defined in `app/modules/sessions/api.py`.

## How to get to it
Run agent-lb and call its HTTP routes.

## Driving it
Use an HTTP client with the required credentials for the route.

## Gotchas
The inventory derives literal routes from source decorators. Router prefixes are represented by endpoint templates in this file.
