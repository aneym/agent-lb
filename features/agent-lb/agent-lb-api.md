---
id: agent-lb-api
product: agent-lb
owner: weekend-finish
status: live
covers: [route:/health, route:/api/accounts, route:/api/api-keys, route:/api/audit-logs, route:/api/conversation-archive/files, route:/api/dashboard/overview, route:/api/dashboard-auth/session, route:/api/federation/checkin, route:/api/firewall/ips, route:/api/oauth/start, route:/api/pools, route:/backend-api/codex/responses, route:/v1/chat/completions, route:/api/usage/public, route:/api/quota-planner/forecast, route:/api/receipts/summary, route:/api/reports, route:/api/request-logs, route:/api/routing-policy/versions, route:/api/menu, route:/api/runtime/version, route:/api/sessions, route:/api/settings, route:/api/sticky-sessions, route:/api/team/members, route:/api/usage/instances, route:/api/accounts/resume-at, route:/api/accounts/availability, route:/api/account-resume-schedules]
paths: ["app/modules/", "app/main.py", "features/*.md"]
proofs:
  - {"id": "health-flow", "kind": "e2e", "cmd": "uv run pytest tests/e2e/test_health_flow.py", "timeout_s": 300, "side_effects": ["none"], "floor": false}
  - {"id": "api-key-lifecycle", "kind": "e2e", "cmd": "uv run pytest tests/e2e/test_api_key_lifecycle.py", "timeout_s": 300, "side_effects": ["writes-test-data"], "floor": false}
unmapped:
  - {"path": "app/modules/limit_warmup/", "date": "2026-10-18", "reason": "service module without a registered HTTP router"}
  - {"path": "app/modules/shared/", "date": "2026-10-18", "reason": "shared support code, not a shippable surface"}
---
# Agent-lb API

## Sub-features
Dashboard APIs, proxy-compatible model APIs, federation, health, and operator endpoints.

## How to get to it
Run agent-lb and use its HTTP API.

## Driving it
Use the dashboard for operator workflows and API clients for proxy and federation workflows.

## Gotchas
Dashboard routes require a session; proxy routes require a configured API key. Route paths may inherit router prefixes.
