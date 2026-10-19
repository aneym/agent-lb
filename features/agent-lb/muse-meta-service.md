---
id: muse-meta-service
product: agent-lb
owner: weekend-finish
status: live
covers: ["route:/muse/v1/models", "route:/muse/v1/responses"]
paths: ["features/agent-lb/muse-meta-service.md"]
proofs: []
unmapped:
  - {"path": "app/modules/proxy/meta_service.py", "date": "2026-10-18", "reason": "dynamic route template /muse-api/{path:path} is unclassifiable"}
---
# Muse Meta Service

## Sub-features
HTTP route handlers defined in `app/modules/proxy/meta_service.py`.

## How to get to it
Run agent-lb and call its HTTP routes.

## Driving it
Use an HTTP client with the required credentials for the route.

## Gotchas
The inventory derives literal routes from source decorators. Router prefixes are represented by endpoint templates in this file.
