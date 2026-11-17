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

## Keys for operator routes
With `AGENT_LB_DASHBOARD_API_KEY_REQUIRED=true`, every route behind the dashboard session check (accounts, pools, settings, API keys, usage, request logs and the rest) answers only a caller with a valid non-member agent-lb key (`Authorization: Bearer sk-clb-…` or `x-api-key`), a password session where the mode is `standard`, or a browser whose resolved address is in `AGENT_LB_DASHBOARD_KEYLESS_CLIENT_CIDRS`. A keyless loopback caller gets 401 `api_key_required`. Local callers read their own key from agent-secret: `agent-lb-key-route`, `agent-lb-key-claude-launch`, `agent-lb-key-status-cli`, `agent-lb-key-reset-cli`, `agent-lb-key-pool-reset`, `agent-lb-key-reauth`, `agent-lb-key-usage-export`, `agent-lb-key-t3-collector`, `agent-lb-key-account-health`, `agent-lb-key-menubar`.

The keyless list matches the real peer address. A forwarded address counts only when the request also carries `X-Agent-Lb-Ingress-Proof` equal to the file at `AGENT_LB_DASHBOARD_KEYLESS_PROXY_PROOF_FILE`; the primary strips that header from every request. On ax42 the tailnet ingress sets it on its tailnet site and on a jobs-only unix socket that Studio's tunnel forwards its tailscale serve to, so a process on ax42 loopback cannot claim one of the operator's addresses. uvicorn runs with `proxy_headers=False`, so `request.client` is always the socket peer and only the app's own trusted-proxy rules read `X-Forwarded-For`.
