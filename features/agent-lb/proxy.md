---
id: proxy
product: agent-lb
owner: weekend-finish
status: live
covers: ["route:/api/anthropic/session-route", "route:/api/codex/usage", "route:/backend-api/codex/agent-identities/jwks", "route:/backend-api/codex/alpha/search", "route:/backend-api/codex/analytics-events/events", "route:/backend-api/codex/memories/trace_summarize", "route:/backend-api/codex/models", "route:/backend-api/codex/opportunistic/admission", "route:/backend-api/codex/realtime/calls", "route:/backend-api/codex/responses", "route:/backend-api/codex/responses/compact", "route:/backend-api/codex/safety/arc", "route:/backend-api/codex/thread/goal/clear", "route:/backend-api/codex/thread/goal/get", "route:/backend-api/codex/thread/goal/set", "route:/backend-api/files", "route:/backend-api/transcribe", "route:/backend-api/wham/agent-identities/jwks", "route:/internal/bridge/responses", "route:/v1/audio/transcriptions", "route:/v1/ccgpt/messages", "route:/v1/ccgpt/messages/count_tokens", "route:/v1/chat/completions", "route:/v1/images/edits", "route:/v1/images/generations", "route:/v1/images/variations", "route:/v1/messages", "route:/v1/messages/count_tokens", "route:/v1/models", "route:/v1/responses", "route:/v1/responses/compact", "route:/v1/usage", "route:/v1/warmup", "route:/v1beta/models"]
paths: ["features/agent-lb/proxy.md"]
proofs:
  - {"id": "provider-response", "kind": "journey", "cmd": "python3 scripts/test-identity/journeys.py provider-response", "timeout_s": 90, "side_effects": ["network", "writes-test-data"], "floor": false}
unmapped:
  - {"path": "app/modules/proxy/api.py", "date": "2026-10-18", "reason": "dynamic route template /api/frame/{path:path} is unclassifiable"}
  - {"path": "app/modules/proxy/api.py", "date": "2026-10-18", "reason": "dynamic route template /backend-api/files/{file_id}/uploaded is unclassifiable"}
  - {"path": "app/modules/proxy/api.py", "date": "2026-10-18", "reason": "dynamic route template /v1/warmup/{mode} is unclassifiable"}
  - {"path": "app/modules/proxy/gemini_routes.py", "date": "2026-11-20", "reason": "dynamic route template /v1beta/models/{name} is unclassifiable"}
  - {"path": "app/modules/proxy/gemini_routes.py", "date": "2026-11-20", "reason": "dynamic route template /v1beta/models/{target} is unclassifiable"}
---
# Proxy

## Sub-features
HTTP route handlers defined in `app/modules/proxy/api.py`.

## How to get to it
Run agent-lb and call its HTTP routes.

## Driving it
Use an HTTP client with the required credentials for the route.

## Gotchas
The inventory derives literal routes from source decorators. Router prefixes are represented by endpoint templates in this file.
`/api/codex/usage` answers `chatgpt-account-id: agent-lb-pool` with pooled usage for keyless trusted clients; `scripts/codex-pool-identity` sets that up for a Codex home with no login (T3 Code's usage page).
`/v1beta` speaks the Gemini API for team keys (`Authorization: Bearer` or `x-goog-api-key`): it lists Gemini 4 Neon and its effort ids and streams `generateContent` through a gemini account. `AGENT_LB_GEMINI_UPSTREAM=fake` swaps the upstream for a local fake; without a gemini account every call answers a Gemini-shaped `no_gemini_account` error. Gemini accounts never leave this instance through federation.
