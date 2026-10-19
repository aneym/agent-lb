---
id: firewall
product: agent-lb
owner: weekend-finish
status: live
covers: ["route:/api/firewall/ips"]
paths: ["features/agent-lb/firewall.md"]
proofs: []
unmapped:
  - {"path": "app/modules/firewall/api.py", "date": "2026-10-18", "reason": "dynamic route template /api/firewall/ips/{ip_address} is unclassifiable"}
---
# Firewall

## Sub-features
HTTP route handlers defined in `app/modules/firewall/api.py`.

## How to get to it
Run agent-lb and call its HTTP routes.

## Driving it
Use an HTTP client with the required credentials for the route.

## Gotchas
The inventory derives literal routes from source decorators. Router prefixes are represented by endpoint templates in this file.
