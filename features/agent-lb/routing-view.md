---
id: routing-view
product: agent-lb
owner: weekend-finish
status: live
covers: ["route:/api/menu", "route:/api/route-decisions"]
paths: ["features/agent-lb/routing-view.md"]
proofs: []
---
# Routing View

## Sub-features
HTTP route handlers defined in `app/modules/routing_view/api.py`.

## How to get to it
Run agent-lb and call its HTTP routes.

## Driving it
Use an HTTP client with the required credentials for the route.

## Gotchas
The inventory derives literal routes from source decorators. Router prefixes are represented by endpoint templates in this file.
