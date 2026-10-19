---
id: settings
product: agent-lb
owner: weekend-finish
status: live
covers: ["route:/api/settings/runtime/connect-address"]
paths: ["features/agent-lb/settings.md"]
proofs: []
---
# Settings

## Sub-features
HTTP route handlers defined in `app/modules/settings/api.py`.

## How to get to it
Run agent-lb and call its HTTP routes.

## Driving it
Use an HTTP client with the required credentials for the route.

## Gotchas
The inventory derives literal routes from source decorators. Router prefixes are represented by endpoint templates in this file.
