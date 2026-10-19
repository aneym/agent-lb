---
id: federation
product: agent-lb
owner: weekend-finish
status: live
covers: ["route:/api/federation/checkin", "route:/api/federation/checkin/execute", "route:/api/federation/checkout", "route:/api/federation/checkout/confirm", "route:/api/federation/checkout/execute", "route:/api/federation/mirror", "route:/api/federation/push", "route:/api/federation/request-logs", "route:/api/federation/status", "route:/api/federation/usage-report"]
paths: ["features/agent-lb/federation.md"]
proofs: []
unmapped:
  - {"path": "app/modules/federation/api.py", "date": "2026-10-18", "reason": "dynamic route template /api/federation/reclaim/{account_id} is unclassifiable"}
  - {"path": "app/modules/federation/api.py", "date": "2026-10-18", "reason": "dynamic route template /api/federation/transfers/{nonce} is unclassifiable"}
  - {"path": "app/modules/federation/api.py", "date": "2026-10-18", "reason": "dynamic route template /api/federation/transfers/{nonce}/abort is unclassifiable"}
---
# Federation

## Sub-features
HTTP route handlers defined in `app/modules/federation/api.py`.

## How to get to it
Run agent-lb and call its HTTP routes.

## Driving it
Use an HTTP client with the required credentials for the route.

## Gotchas
The inventory derives literal routes from source decorators. Router prefixes are represented by endpoint templates in this file.
