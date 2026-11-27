---
id: federation
product: agent-lb
owner: weekend-finish
status: live
covers: ["route:/api/federation/checkin", "route:/api/federation/checkin/execute", "route:/api/federation/checkout", "route:/api/federation/checkout/confirm", "route:/api/federation/checkout/execute", "route:/api/federation/mirror", "route:/api/federation/mirror/nudge", "route:/api/federation/push", "route:/api/federation/request-logs", "route:/api/federation/status", "route:/api/federation/sticky-pins", "route:/api/federation/usage-report"]
paths: ["features/agent-lb/federation.md"]
proofs: []
unmapped:
  - {"path": "app/modules/federation/api.py", "date": "2026-10-18", "reason": "dynamic route template /api/federation/reclaim/{account_id} is unclassifiable"}
  - {"path": "app/modules/federation/api.py", "date": "2026-10-18", "reason": "dynamic route template /api/federation/transfers/{nonce} is unclassifiable"}
  - {"path": "app/modules/federation/api.py", "date": "2026-10-18", "reason": "dynamic route template /api/federation/transfers/{nonce}/abort is unclassifiable"}
---
# Federation

## Sub-features
HTTP route handlers defined in `app/modules/federation/api.py`.

## How to get to it
Run agent-lb and call its HTTP routes.

## Driving it
Use an HTTP client with the required credentials for the route.

Edges trade thread-to-account pins with the primary through `/api/federation/sticky-pins`; see `docs/sticky-pins-across-routers.md`.

Set `AGENT_LB_FEDERATION_MIRROR_NUDGE_URL` on the primary to the comma-separated origins of the edges. Each edge uses the same mirror bearer and pulls from its configured peer after an authenticated `POST /api/federation/mirror/nudge`. A failed nudge leaves the normal pull interval in place.

The primary publishes a presence list separately from the accounts whose access credentials it can share. Edges use it to remove deleted peer mirrors without removing local accounts or accounts with custody history. Old peers without a presence list do not trigger pruning.

Push source authentication uses the receiving host's local `tailscale whois` CLI. On Linux, it prefers `/usr/bin/tailscale`. An expired foreign owner's token is marked `ownerStale` in account summaries without changing the account's lifecycle status.

## Gotchas
The inventory derives literal routes from source decorators. Router prefixes are represented by endpoint templates in this file.
