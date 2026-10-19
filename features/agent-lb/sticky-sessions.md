---
id: sticky-sessions
product: agent-lb
owner: weekend-finish
status: live
covers: ["route:/api/sticky-sessions/delete", "route:/api/sticky-sessions/delete-filtered", "route:/api/sticky-sessions/purge"]
paths: ["features/agent-lb/sticky-sessions.md"]
proofs: []
unmapped:
  - {"path": "app/modules/sticky_sessions/api.py", "date": "2026-10-18", "reason": "dynamic route template /api/sticky-sessions/{kind}/{key:path} is unclassifiable"}
---
# Sticky Sessions

## Sub-features
HTTP route handlers defined in `app/modules/sticky_sessions/api.py`.

## How to get to it
Run agent-lb and call its HTTP routes.

## Driving it
Use an HTTP client with the required credentials for the route.

## Gotchas
The inventory derives literal routes from source decorators. Router prefixes are represented by endpoint templates in this file.
