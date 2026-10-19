---
id: oauth
product: agent-lb
owner: weekend-finish
status: live
covers: ["route:/api/oauth/complete", "route:/api/oauth/manual-callback", "route:/api/oauth/start", "route:/api/oauth/status"]
paths: ["features/agent-lb/oauth.md"]
proofs: []
---
# Oauth

## Sub-features
HTTP route handlers defined in `app/modules/oauth/api.py`.

## How to get to it
Run agent-lb and call its HTTP routes.

## Driving it
Use an HTTP client with the required credentials for the route.

## Gotchas
The inventory derives literal routes from source decorators. Router prefixes are represented by endpoint templates in this file.
