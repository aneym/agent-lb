---
id: accounts
product: agent-lb
owner: weekend-finish
status: live
covers: ["route:/api/accounts/import", "route:/api/accounts/import/api-key"]
paths: ["app/modules/accounts/api.py", "app/modules/accounts/availability_api.py", "features/agent-lb/accounts.md"]
proofs: []
unmapped:
  - {"path": "app/modules/accounts/api.py", "date": "2026-10-18", "reason": "dynamic route template /api/accounts/{account_id} is unclassifiable"}
  - {"path": "app/modules/accounts/api.py", "date": "2026-10-18", "reason": "dynamic route template /api/accounts/{account_id}/alias is unclassifiable"}
  - {"path": "app/modules/accounts/api.py", "date": "2026-10-18", "reason": "dynamic route template /api/accounts/{account_id}/exchange-reset is unclassifiable"}
  - {"path": "app/modules/accounts/api.py", "date": "2026-10-18", "reason": "dynamic route template /api/accounts/{account_id}/export is unclassifiable"}
  - {"path": "app/modules/accounts/api.py", "date": "2026-10-18", "reason": "dynamic route template /api/accounts/{account_id}/export/auth is unclassifiable"}
  - {"path": "app/modules/accounts/api.py", "date": "2026-10-18", "reason": "dynamic route template /api/accounts/{account_id}/export/opencode-auth is unclassifiable"}
  - {"path": "app/modules/accounts/api.py", "date": "2026-10-18", "reason": "dynamic route template /api/accounts/{account_id}/limit-warmup is unclassifiable"}
  - {"path": "app/modules/accounts/api.py", "date": "2026-10-18", "reason": "dynamic route template /api/accounts/{account_id}/pause is unclassifiable"}
  - {"path": "app/modules/accounts/api.py", "date": "2026-10-18", "reason": "dynamic route template /api/accounts/{account_id}/probe is unclassifiable"}
  - {"path": "app/modules/accounts/api.py", "date": "2026-10-18", "reason": "dynamic route template /api/accounts/{account_id}/rate-limit-reset-credits is unclassifiable"}
  - {"path": "app/modules/accounts/api.py", "date": "2026-10-18", "reason": "dynamic route template /api/accounts/{account_id}/rate-limit-reset-credits/consume is unclassifiable"}
  - {"path": "app/modules/accounts/api.py", "date": "2026-10-18", "reason": "dynamic route template /api/accounts/{account_id}/reactivate is unclassifiable"}
  - {"path": "app/modules/accounts/api.py", "date": "2026-10-18", "reason": "dynamic route template /api/accounts/{account_id}/routing-policy is unclassifiable"}
  - {"path": "app/modules/accounts/api.py", "date": "2026-10-18", "reason": "dynamic route template /api/accounts/{account_id}/subscription is unclassifiable"}
  - {"path": "app/modules/accounts/api.py", "date": "2026-10-18", "reason": "dynamic route template /api/accounts/{account_id}/subscription/check is unclassifiable"}
  - {"path": "app/modules/accounts/api.py", "date": "2026-10-18", "reason": "dynamic route template /api/accounts/{account_id}/trends is unclassifiable"}
---
# Accounts

## Sub-features
HTTP route handlers defined in `app/modules/accounts/api.py`.

## How to get to it
Run agent-lb and call its HTTP routes.

## Driving it
Use an HTTP client with the required credentials for the route.

## Gotchas
The inventory derives literal routes from source decorators. Router prefixes are represented by endpoint templates in this file. The account availability router is defined in `app/modules/accounts/availability_api.py`.
