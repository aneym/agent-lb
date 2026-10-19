---
id: quota-planner
product: agent-lb
owner: weekend-finish
status: live
covers: ["route:/api/quota-planner/decisions", "route:/api/quota-planner/forecast", "route:/api/quota-planner/settings", "route:/api/quota-planner/warm-now"]
paths: ["features/agent-lb/quota-planner.md"]
proofs: []
unmapped:
  - {"path": "app/modules/quota_planner/api.py", "date": "2026-10-18", "reason": "dynamic route template /api/quota-planner/decisions/{decision_id}/cancel is unclassifiable"}
---
# Quota Planner

## Sub-features
HTTP route handlers defined in `app/modules/quota_planner/api.py`.

## How to get to it
Run agent-lb and call its HTTP routes.

## Driving it
Use an HTTP client with the required credentials for the route.

## Gotchas
The inventory derives literal routes from source decorators. Router prefixes are represented by endpoint templates in this file.
