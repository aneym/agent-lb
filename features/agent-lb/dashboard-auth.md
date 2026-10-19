---
id: dashboard-auth
product: agent-lb
owner: weekend-finish
status: live
covers: ["route:/api/dashboard-auth/logout", "route:/api/dashboard-auth/password", "route:/api/dashboard-auth/password/change", "route:/api/dashboard-auth/password/login", "route:/api/dashboard-auth/password/setup", "route:/api/dashboard-auth/session", "route:/api/dashboard-auth/totp/disable", "route:/api/dashboard-auth/totp/setup/confirm", "route:/api/dashboard-auth/totp/setup/start", "route:/api/dashboard-auth/totp/verify"]
paths: ["features/agent-lb/dashboard-auth.md"]
proofs: []
---
# Dashboard Auth

## Sub-features
HTTP route handlers defined in `app/modules/dashboard_auth/api.py`.

## How to get to it
Run agent-lb and call its HTTP routes.

## Driving it
Use an HTTP client with the required credentials for the route.

## Gotchas
The inventory derives literal routes from source decorators. Router prefixes are represented by endpoint templates in this file.
