---
id: proxy
product: agent-lb
owner: weekend-finish
status: live
covers: ["route:/api/anthropic/session-route", "route:/api/codex/usage", "route:/backend-api/codex/agent-identities/jwks", "route:/backend-api/codex/alpha/search", "route:/backend-api/codex/analytics-events/events", "route:/backend-api/codex/memories/trace_summarize", "route:/backend-api/codex/models", "route:/backend-api/codex/opportunistic/admission", "route:/backend-api/codex/realtime/calls", "route:/backend-api/codex/responses", "route:/backend-api/codex/responses/compact", "route:/backend-api/codex/safety/arc", "route:/backend-api/codex/thread/goal/clear", "route:/backend-api/codex/thread/goal/get", "route:/backend-api/codex/thread/goal/set", "route:/backend-api/files", "route:/backend-api/transcribe", "route:/backend-api/wham/agent-identities/jwks", "route:/internal/bridge/responses", "route:/v1/audio/transcriptions", "route:/v1/ccgpt/messages", "route:/v1/ccgpt/messages/count_tokens", "route:/v1/chat/completions", "route:/v1/images/edits", "route:/v1/images/generations", "route:/v1/images/variations", "route:/v1/messages", "route:/v1/messages/count_tokens", "route:/v1/models", "route:/v1/responses", "route:/v1/responses/compact", "route:/v1/usage", "route:/v1/warmup"]
paths: ["features/agent-lb/proxy.md"]
proofs:
  - {"id": "provider-response", "kind": "journey", "cmd": "python3 scripts/test-identity/journeys.py provider-response", "timeout_s": 90, "side_effects": ["network", "writes-test-data"], "floor": false}
unmapped:
  - {"path": "app/modules/proxy/api.py", "date": "2026-10-18", "reason": "dynamic route template /api/frame/{path:path} is unclassifiable"}
  - {"path": "app/modules/proxy/api.py", "date": "2026-10-18", "reason": "dynamic route template /backend-api/files/{file_id}/uploaded is unclassifiable"}
  - {"path": "app/modules/proxy/api.py", "date": "2026-10-18", "reason": "dynamic route template /v1/warmup/{mode} is unclassifiable"}
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
