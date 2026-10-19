---
id: account-schedule
product: agent-lb
owner: weekend-finish
status: live
covers: ["route:/api/account-resume-schedules"]
paths: ["features/agent-lb/account-schedule.md"]
proofs: []
unmapped:
  - {"path": "app/modules/account_schedule/api.py", "date": "2026-10-18", "reason": "dynamic route template /api/accounts/{account_id}/resume-at is unclassifiable"}
---
# Account Schedule

## Sub-features
HTTP route handlers defined in `app/modules/account_schedule/api.py`.

## How to get to it
Run agent-lb and call its HTTP routes.

## Driving it
Use an HTTP client with the required credentials for the route.

## Gotchas
The inventory derives literal routes from source decorators. Router prefixes are represented by endpoint templates in this file.
