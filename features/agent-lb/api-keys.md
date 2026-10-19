---
id: api-keys
product: agent-lb
owner: weekend-finish
status: live
covers: ["route:/api/api-keys"]
paths: ["features/agent-lb/api-keys.md"]
proofs: []
unmapped:
  - {"path": "app/modules/api_keys/api.py", "date": "2026-10-18", "reason": "dynamic route template /api/api-keys/{key_id} is unclassifiable"}
  - {"path": "app/modules/api_keys/api.py", "date": "2026-10-18", "reason": "dynamic route template /api/api-keys/{key_id}/regenerate is unclassifiable"}
  - {"path": "app/modules/api_keys/api.py", "date": "2026-10-18", "reason": "dynamic route template /api/api-keys/{key_id}/trends is unclassifiable"}
  - {"path": "app/modules/api_keys/api.py", "date": "2026-10-18", "reason": "dynamic route template /api/api-keys/{key_id}/usage-7d is unclassifiable"}
---
# Api Keys

## Sub-features
HTTP route handlers defined in `app/modules/api_keys/api.py`.

## How to get to it
Run agent-lb and call its HTTP routes.

## Driving it
Use an HTTP client with the required credentials for the route.

## Gotchas
The inventory derives literal routes from source decorators. Router prefixes are represented by endpoint templates in this file.
