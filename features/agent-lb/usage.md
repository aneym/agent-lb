---
id: usage
product: agent-lb
owner: weekend-finish
status: live
covers: ["route:/api/usage/callers", "route:/api/usage/history", "route:/api/usage/instances", "route:/api/usage/summary", "route:/api/usage/window"]
paths: ["features/agent-lb/usage.md"]
proofs: []
---
# Usage

## Sub-features
HTTP route handlers defined in `app/modules/usage/api.py`.

## How to get to it
Run agent-lb and call its HTTP routes.

## Driving it
Use an HTTP client with the required credentials for the route.

## Gotchas
The inventory derives literal routes from source decorators. Router prefixes are represented by endpoint templates in this file.
