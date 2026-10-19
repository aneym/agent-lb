---
id: team
product: agent-lb
owner: weekend-finish
status: live
covers: ["route:/api/team/members"]
paths: ["features/agent-lb/team.md"]
proofs: []
unmapped:
  - {"path": "app/modules/team/api.py", "date": "2026-10-18", "reason": "dynamic route template /api/team/members/{member_id} is unclassifiable"}
  - {"path": "app/modules/team/api.py", "date": "2026-10-18", "reason": "dynamic route template /api/team/members/{member_id}/keys is unclassifiable"}
  - {"path": "app/modules/team/api.py", "date": "2026-10-18", "reason": "dynamic route template /api/team/members/{member_id}/onboarding is unclassifiable"}
  - {"path": "app/modules/team/api.py", "date": "2026-10-18", "reason": "dynamic route template /api/team/members/{member_id}/usage is unclassifiable"}
---
# Team

## Sub-features
HTTP route handlers defined in `app/modules/team/api.py`.

## How to get to it
Run agent-lb and call its HTTP routes.

## Driving it
Use an HTTP client with the required credentials for the route.

## Gotchas
The inventory derives literal routes from source decorators. Router prefixes are represented by endpoint templates in this file.
