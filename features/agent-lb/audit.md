---
id: audit
product: agent-lb
owner: weekend-finish
status: live
covers: []
paths: ["features/agent-lb/audit.md"]
proofs: []
---
# Audit

## Sub-features
HTTP route handlers defined in `app/modules/audit/api.py`.

## How to get to it
Run agent-lb and call its HTTP routes.

## Driving it
Use an HTTP client with the required credentials for the route.

## Gotchas
The inventory derives literal routes from source decorators. Router prefixes are represented by endpoint templates in this file.
