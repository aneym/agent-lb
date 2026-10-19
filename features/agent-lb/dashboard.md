---
id: dashboard
product: agent-lb
owner: weekend-finish
status: live
covers: ["route:/api/dashboard/overview", "route:/api/dashboard/projections", "route:/api/models"]
paths: ["features/agent-lb/dashboard.md"]
proofs: []
---
# Dashboard

## Sub-features
HTTP route handlers defined in `app/modules/dashboard/api.py`.

## How to get to it
Run agent-lb and call its HTTP routes.

## Driving it
Use an HTTP client with the required credentials for the route.

## Gotchas
The inventory derives literal routes from source decorators. Router prefixes are represented by endpoint templates in this file.
