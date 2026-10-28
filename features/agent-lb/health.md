---
id: health
product: agent-lb
owner: weekend-finish
status: live
covers: ["route:/health", "route:/api/health", "route:/api/hello", "route:/health/live", "route:/health/ready", "route:/health/db-pool", "route:/health/startup", "route:/internal/drain/start", "route:/internal/drain/status", "route:/internal/drain/stop", "route:/internal/drain/close-websockets", "route:/internal/oauth/pending"]
paths: ["features/agent-lb/health.md"]
proofs: []
---
# Health

## Sub-features
HTTP route handlers defined in `app/modules/health/api.py`.

## How to get to it
Run agent-lb and call its HTTP routes.

## Driving it
Use an HTTP client with the required credentials for the route.

## Gotchas
The inventory derives literal routes from source decorators. Router prefixes are represented by endpoint templates in this file.
