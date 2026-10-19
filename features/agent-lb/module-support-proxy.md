---
id: module-support-proxy
product: agent-lb
owner: weekend-finish
status: live
covers: []
paths: ["app/modules/proxy/", "features/agent-lb/module-support-proxy.md"]
proofs: []
unmapped:
  - {"path":"app/modules/proxy/durable_bridge_snapshot.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/proxy/service.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/proxy/tool_call_keys.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/proxy/ring_membership.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/proxy/selection_diagnostics.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/proxy/api_key_usage.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/proxy/additional_model_limits.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/proxy/request_policy.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/proxy/sticky_repository.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/proxy/durable_bridge_coordinator.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/proxy/image_stream.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/proxy/tool_call_replay.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/proxy/load_balancer.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/proxy/work_admission.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/proxy/affinity.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/proxy/__init__.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/proxy/stand_in.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/proxy/image_usage.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/proxy/types.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/proxy/tool_call_dedupe.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/proxy/schemas.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/proxy/repo_bundle.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/proxy/account_cache.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/proxy/anthropic_service.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/proxy/rate_limit_cache.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/proxy/images_service.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/proxy/claude_codex_bridge.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/proxy/helpers.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/proxy/http_bridge_forwarding.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/proxy/durable_bridge_repository.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/proxy/_service/response_create.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/proxy/_service/codex_control_context.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/proxy/_service/support.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/proxy/_service/api_key_usage.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/proxy/_service/response_create_diagnostics.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/proxy/_service/file_transfer.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/proxy/_service/compact.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/proxy/_service/file_ops.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/proxy/_service/__init__.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/proxy/_service/observability.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/proxy/_service/codex_control.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/proxy/_service/warmup.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/proxy/_service/transcribe.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/proxy/_service/compaction_breaker.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/proxy/_service/rate_limit.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/proxy/_service/request_log.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/proxy/_service/websocket/mixin.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/proxy/_service/websocket/protocol.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/proxy/_service/websocket/__init__.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/proxy/_service/websocket/helpers.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/proxy/_service/streaming/mixin.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/proxy/_service/streaming/protocol.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/proxy/_service/streaming/__init__.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/proxy/_service/streaming/retry.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/proxy/_service/streaming/helpers.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/proxy/_service/http_bridge/service_stubs.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/proxy/_service/http_bridge/mixin.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/proxy/_service/http_bridge/request_submit.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/proxy/_service/http_bridge/protocol.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/proxy/_service/http_bridge/__init__.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/proxy/_service/http_bridge/streaming.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/proxy/_service/http_bridge/owner_forwarding.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/proxy/_service/http_bridge/helpers.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/proxy/_service/http_bridge/session_registry.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
  - {"path":"app/modules/proxy/_service/http_bridge/upstream_events.py","date":"2026-10-18","reason":"support module without its own HTTP route surface"}
---
# Proxy support

## Sub-features
Internal service, schema, repository, or scheduler modules in this route group.

## How to get to it
Use the public HTTP route feature for this group.

## Driving it
These modules support the route feature and are not directly invoked.

## Gotchas
Internal files may be shared across route handlers.
