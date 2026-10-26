---
id: pools
product: agent-lb
owner: weekend-finish
status: live
covers: ["route:/api/pools/pace", "route:/api/pools/plan", "route:/api/pools/reservations", "route:/api/pools/seat-accounts", "route:/api/pools/stand-ins"]
paths: ["features/agent-lb/pools.md"]
proofs: []
unmapped:
  - {"path": "app/modules/pools/api.py", "date": "2026-10-18", "reason": "dynamic route template /api/pools/reservations/{reservation_id}/heartbeat is unclassifiable"}
  - {"path": "app/modules/pools/api.py", "date": "2026-10-18", "reason": "dynamic route template /api/pools/reservations/{reservation_id}/release is unclassifiable"}
---
# Pools

## Sub-features
HTTP route handlers defined in `app/modules/pools/api.py`.

## How to get to it
Run agent-lb and call its HTTP routes.

## Driving it
Use an HTTP client with the required credentials for the route.

## Gotchas
The inventory derives literal routes from source decorators. Router prefixes are represented by endpoint templates in this file.

## Pace
`/api/pools/pace` reports burn ÷ sustainable per pool. How to read it, measure burn after a change, and attribute burn to sources: `docs/burn-audit.md`.
