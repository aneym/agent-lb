---
id: routing-policy
product: agent-lb
owner: weekend-finish
status: live
covers: ["route:/api/routing-policy/drafts", "route:/api/routing-policy/versions"]
paths: ["features/agent-lb/routing-policy.md"]
proofs: []
unmapped:
  - {"path": "app/modules/routing_policy/api.py", "date": "2026-10-18", "reason": "dynamic route template /api/routing-policy/drafts/{version} is unclassifiable"}
  - {"path": "app/modules/routing_policy/api.py", "date": "2026-10-18", "reason": "dynamic route template /api/routing-policy/drafts/{version}/approve is unclassifiable"}
  - {"path": "app/modules/routing_policy/api.py", "date": "2026-10-18", "reason": "dynamic route template /api/routing-policy/versions/{version} is unclassifiable"}
---
# Routing Policy

## Sub-features
HTTP route handlers defined in `app/modules/routing_policy/api.py`.

## How to get to it
Run agent-lb and call its HTTP routes.

## Driving it
Use an HTTP client with the required credentials for the route.

## Gotchas
The inventory derives literal routes from source decorators. Router prefixes are represented by endpoint templates in this file.
