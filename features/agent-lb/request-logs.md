---
id: request-logs
product: agent-lb
owner: weekend-finish
status: live
covers: ["route:/api/request-logs/anthropic-cache-summary", "route:/api/request-logs/http-status-summary", "route:/api/request-logs/options"]
paths: ["features/agent-lb/request-logs.md"]
proofs:
  - {"id": "inspect-failed-request", "kind": "journey", "cmd": "python3 scripts/test-identity/journeys.py inspect-failed-request", "timeout_s": 60, "side_effects": ["network", "writes-test-data"], "floor": false}
---
# Request Logs

## Sub-features
HTTP route handlers defined in `app/modules/request_logs/api.py`.

## How to get to it
Run agent-lb and call its HTTP routes.

## Driving it
Use an HTTP client with the required credentials for the route.

## Gotchas
The inventory derives literal routes from source decorators. Router prefixes are represented by endpoint templates in this file.
