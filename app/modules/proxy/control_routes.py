from __future__ import annotations

import time
from collections.abc import Mapping
from datetime import datetime, timezone
from json import JSONDecodeError
from typing import cast

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response, Security
from fastapi.responses import JSONResponse

from app.core.audit.service import AuditService
from app.core.auth.dependencies import validate_proxy_api_key
from app.core.clients.proxy import ProxyResponseError
from app.core.errors import openai_error
from app.core.openai.v1_requests import V1ResponsesRequest
from app.core.types import JsonValue
from app.core.utils.request_id import get_request_id
from app.dependencies import AnthropicProxyContext, ProxyContext, get_anthropic_proxy_context, get_proxy_context
from app.modules.api_keys.service_data import ApiKeyData
from app.modules.proxy._service.compaction_breaker import (
    COMPACTION_RETRY_THRESHOLD,
    COMPACTION_RETRY_WINDOW_SECONDS,
    compaction_fingerprint,
    get_compaction_retry_breaker,
)
from app.modules.proxy.anthropic_errors import openai_error_type_for_status, retry_headers
from app.modules.proxy.anthropic_service import AnthropicProxyError
from app.modules.proxy.api_dependencies import proxy_dependencies
from app.modules.proxy.constants import (
    ANTHROPIC_SESSION_ROUTE_AFFINITY_QUOTA_KEYS,
    ANTHROPIC_SESSION_ROUTE_QUOTA_KEYS,
    CODEX_CONTROL_RESPONSE_HEADERS,
)
from app.modules.proxy.response_errors import logged_error_json_response

control_routes_usage_router = APIRouter(prefix="")


control_routes_router = APIRouter(prefix="/backend-api/codex")


control_routes_wham_router = APIRouter(prefix="/backend-api/wham")


@control_routes_usage_router.post("/api/anthropic/session-route", response_model=None)
async def claim_anthropic_session_route(
    payload: dict[str, str] = Body(...),
    context: AnthropicProxyContext = Depends(get_anthropic_proxy_context),
) -> dict[str, str | None] | JSONResponse:
    session_id = (payload.get("sessionId") or "").strip()
    model = (payload.get("model") or "claude-opus-5-5").strip()
    quota_key = (payload.get("quotaKey") or "anthropic_top_thinking").strip()
    affinity_quota_key = (payload.get("affinityQuotaKey") or "").strip()
    if not affinity_quota_key:
        affinity_quota_key = default_anthropic_affinity_quota_key(model, quota_key)
    if not session_id:
        raise HTTPException(status_code=400, detail="sessionId is required")
    if quota_key not in ANTHROPIC_SESSION_ROUTE_QUOTA_KEYS:
        raise HTTPException(status_code=400, detail="quotaKey is invalid")
    if affinity_quota_key not in ANTHROPIC_SESSION_ROUTE_AFFINITY_QUOTA_KEYS:
        raise HTTPException(status_code=400, detail="affinityQuotaKey is invalid")
    try:
        account = await context.service.claim_session_route(
            session_id=session_id,
            model=model,
            quota_key=quota_key,
            affinity_quota_key=affinity_quota_key,
        )
    except AnthropicProxyError as exc:
        content = openai_error(
            exc.code,
            exc.message,
            error_type=openai_error_type_for_status(exc.status_code),
        )
        if exc.retry_at is not None and isinstance(content.get("error"), dict):
            content["error"]["retryAt"] = (
                datetime.fromtimestamp(exc.retry_at, tz=timezone.utc).isoformat().replace("+00:00", "Z")
            )
            content["error"]["retryAfterSeconds"] = max(0, int(exc.retry_at) - int(time.time()))
        return JSONResponse(
            status_code=exc.status_code,
            content=content,
            headers=retry_headers(exc.retry_at),
        )
    return {
        "accountId": account.id,
        "email": account.email,
        "alias": account.alias,
        "model": model,
        "quotaKey": quota_key,
        "affinityQuotaKey": affinity_quota_key,
    }


def default_anthropic_affinity_quota_key(model: str, quota_key: str) -> str:
    if quota_key != "anthropic_fast":
        resolved = quota_key
    elif "haiku" in model.lower():
        resolved = "anthropic_standard"
    else:
        resolved = "anthropic_top_thinking"

    return f"{resolved}_fable" if proxy_dependencies.is_fable_model(model) else resolved


def accepts_event_stream(request: Request) -> bool:
    for value in request.headers.getlist("accept"):
        media_ranges = (part.split(";", 1)[0].strip().lower() for part in value.split(","))
        if "text/event-stream" in media_ranges:
            return True
    return False


def has_openai_responses_shape(payload: V1ResponsesRequest | Mapping[str, JsonValue]) -> bool:
    if isinstance(payload, Mapping):
        payload_dict = cast("Mapping[str, JsonValue]", payload)
        return (
            ("input" in payload_dict and payload_dict.get("instructions") is None)
            or payload_dict.get("messages") is not None
            or "truncation" in payload_dict
        )

    explicit_fields = payload.model_fields_set
    return (
        ("input" in explicit_fields and payload.instructions is None)
        or payload.messages is not None
        or "truncation" in explicit_fields
    )


def is_openai_sdk_request(
    request: Request,
    payload: V1ResponsesRequest | Mapping[str, JsonValue] | None = None,
) -> bool:
    for header_name in request.headers:
        normalized_header = header_name.lower()
        if normalized_header.startswith("x-stainless-"):
            return True
    user_agent = request.headers.get("user-agent", "").lower()
    if "openai" in user_agent:
        return True
    if payload is None or not has_openai_responses_shape(payload):
        return False
    if isinstance(payload, Mapping):
        payload_dict = cast("Mapping[str, JsonValue]", payload)
        return accepts_event_stream(request) or payload_dict.get("messages") is not None
    return accepts_event_stream(request) or payload.messages is not None


def compaction_retry_breaker_response(
    request: Request,
    payload: Mapping[str, JsonValue],
) -> JSONResponse | None:

    fingerprint = compaction_fingerprint(payload.get("model"), payload.get("input"))
    admission = get_compaction_retry_breaker().admit(fingerprint)
    if admission.should_audit:
        AuditService.log_async(
            "compaction_retry_loop_opened",
            details={
                "fingerprint_prefix": fingerprint[:16],
                "count": admission.count,
                "window_seconds": COMPACTION_RETRY_WINDOW_SECONDS,
            },
            request_id=get_request_id(),
        )
    if admission.admitted:
        return None
    message = (
        f"The identical compaction turn repeated more than {COMPACTION_RETRY_THRESHOLD} times "
        f"within {int(COMPACTION_RETRY_WINDOW_SECONDS)} seconds; the client is stuck retrying a "
        "turn that never lands. Wait for Retry-After before resending."
    )
    return logged_error_json_response(
        request,
        429,
        openai_error("compaction_retry_loop", message, error_type="rate_limit_error"),
        headers={"Retry-After": str(int(COMPACTION_RETRY_WINDOW_SECONDS))},
    )


async def thread_goal_payload_from_request(request: Request) -> dict[str, JsonValue]:
    if request.method.upper() == "GET":
        return {key: value for key, value in request.query_params.multi_items()}
    try:
        raw = await request.json()
    except (JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail="thread goal payload must be valid JSON") from exc
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise HTTPException(status_code=400, detail="thread goal payload must be a JSON object")
    return cast(dict[str, JsonValue], raw)


async def thread_goal_proxy(
    request: Request,
    operation: str,
    context: ProxyContext,
    api_key: ApiKeyData | None,
) -> Response:
    payload = await thread_goal_payload_from_request(request)
    try:
        response = await context.service.thread_goal_request(
            operation,
            payload,
            request.headers,
            method=request.method,
            codex_session_affinity=True,
            api_key=api_key,
        )
    except ProxyResponseError as exc:
        return logged_error_json_response(request, exc.status_code, exc.payload)
    return JSONResponse(response)


def codex_control_downstream_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {key: value for key, value in headers.items() if key.lower() in CODEX_CONTROL_RESPONSE_HEADERS}


async def codex_control_proxy(
    request: Request,
    path: str,
    context: ProxyContext,
    api_key: ApiKeyData | None,
) -> Response:
    try:
        response = await context.service.codex_control_request(
            path,
            method=request.method,
            payload=await request.body() if request.method.upper() not in {"GET", "HEAD"} else None,
            query_params=list(request.query_params.multi_items()),
            headers=request.headers,
            codex_session_affinity=True,
            api_key=api_key,
        )
    except ProxyResponseError as exc:
        return logged_error_json_response(request, exc.status_code, exc.payload)
    return Response(
        content=response.body,
        status_code=response.status_code,
        headers=codex_control_downstream_headers(response.headers),
    )


@control_routes_router.api_route("/thread/goal/get", methods=["GET", "POST"])
async def thread_goal_get(
    request: Request,
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> Response:
    return await thread_goal_proxy(request, "get", context, api_key)


@control_routes_router.post("/thread/goal/set")
async def thread_goal_set(
    request: Request,
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> Response:
    return await thread_goal_proxy(request, "set", context, api_key)


@control_routes_router.post("/thread/goal/clear")
async def thread_goal_clear(
    request: Request,
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> Response:
    return await thread_goal_proxy(request, "clear", context, api_key)


@control_routes_router.post("/analytics-events/events")
async def codex_analytics_events(
    request: Request,
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> Response:
    return await codex_control_proxy(request, "analytics-events/events", context, api_key)


@control_routes_router.post("/memories/trace_summarize")
async def codex_memories_trace_summarize(
    request: Request,
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> Response:
    return await codex_control_proxy(request, "memories/trace_summarize", context, api_key)


@control_routes_router.post("/realtime/calls")
async def codex_realtime_calls(
    request: Request,
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> Response:
    return await codex_control_proxy(request, "realtime/calls", context, api_key)


@control_routes_router.post("/safety/arc")
async def codex_safety_arc(
    request: Request,
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> Response:
    return await codex_control_proxy(request, "safety/arc", context, api_key)


@control_routes_router.post("/alpha/search")
async def codex_alpha_search(
    request: Request,
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> Response:

    return await codex_control_proxy(request, "alpha/search", context, api_key)


@control_routes_router.get("/agent-identities/jwks")
async def codex_agent_identities_jwks(
    request: Request,
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> Response:
    return await codex_control_proxy(request, "agent-identities/jwks", context, api_key)


@control_routes_wham_router.get("/agent-identities/jwks")
async def wham_agent_identities_jwks(
    request: Request,
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> Response:
    return await codex_control_proxy(request, "wham/agent-identities/jwks", context, api_key)
