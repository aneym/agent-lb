---
id: runtime
product: agent-lb
owner: weekend-finish
status: live
covers: ["route:/api/runtime/version"]
paths: ["features/agent-lb/runtime.md"]
proofs: []
---
# Runtime

## Sub-features
HTTP route handlers defined in `app/modules/runtime/api.py`.

## How to get to it
Run agent-lb and call its HTTP routes.

## Driving it
Use an HTTP client with the required credentials for the route.

## Gotchas
The inventory derives literal routes from source decorators. Router prefixes are represented by endpoint templates in this file.
