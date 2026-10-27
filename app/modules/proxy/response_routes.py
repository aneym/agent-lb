from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Request, Response, Security, WebSocket
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.core.auth.dependencies import validate_proxy_api_key
from app.core.config.settings import get_settings
from app.core.errors import openai_error
from app.core.exceptions import TeamMemberOverCapError, TeamMemberSuspendedError
from app.core.openai.exceptions import ClientPayloadError
from app.core.openai.models import OpenAIResponseResult
from app.core.openai.requests import ResponsesRequest
from app.core.openai.v1_requests import V1ResponsesRequest
from app.core.types import JsonValue
from app.core.utils.client_session import FACTORY_ATTRIBUTION
from app.dependencies import ProxyContext, get_proxy_context, get_proxy_websocket_context
from app.modules.api_keys.service_data import ApiKeyData
from app.modules.proxy import affinity as proxy_affinity_module
from app.modules.proxy._service.support import request_input_contains_compaction_trigger
from app.modules.proxy.connection_auth import validate_internal_bridge_api_key, validate_proxy_websocket_request
from app.modules.proxy.control_routes import (
    compaction_retry_breaker_response,
    has_openai_responses_shape,
    is_openai_sdk_request,
)
from app.modules.proxy.http_bridge_forwarding import parse_forwarded_request
from app.modules.proxy.request_admission import opportunistic_admission_denial
from app.modules.proxy.request_policy import (
    enforce_strict_function_tools_format,
    enforce_strict_text_format,
    normalize_responses_request_payload,
    openai_client_payload_error,
    openai_validation_error,
)
from app.modules.proxy.response_errors import logged_error_json_response
from app.modules.proxy.response_requests import collect_responses, stream_responses, strip_internal_bridge_headers
from app.modules.team.gate import check_member_gate

response_routes_router = APIRouter(prefix="/backend-api/codex")


response_routes_ws_router = APIRouter(prefix="/backend-api/codex")


response_routes_v1_router = APIRouter(prefix="/v1")


response_routes_internal_router = APIRouter(prefix="/internal/bridge")


response_routes_v1_ws_router = APIRouter(prefix="/v1")


@response_routes_router.post(
    "/responses",
    responses={
        200: {
            "content": {
                "text/event-stream": {
                    "schema": {"type": "string"},
                }
            }
        }
    },
)
async def responses(
    request: Request,
    payload: dict[str, JsonValue] = Body(...),
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> Response:
    is_compaction_turn = request_input_contains_compaction_trigger(payload.get("input"))
    if is_compaction_turn:
        breaker_response = compaction_retry_breaker_response(request, payload)
        if breaker_response is not None:
            return breaker_response
    openai_sdk_request = is_openai_sdk_request(request, payload)

    if openai_sdk_request and is_compaction_turn:
        openai_sdk_request = False
    openai_compat_payload = has_openai_responses_shape(payload)
    try:
        responses_payload = normalize_responses_request_payload(
            payload,
            openai_compat=openai_compat_payload,
        )
    except ClientPayloadError as exc:
        error = openai_client_payload_error(exc)
        return logged_error_json_response(request, 400, error)
    except ValidationError as exc:
        error = openai_validation_error(exc)
        return logged_error_json_response(request, 400, error)

    return await stream_responses(
        request,
        responses_payload,
        context,
        api_key,
        codex_session_affinity=True,
        openai_cache_affinity=True,
        prefer_http_bridge=True,
        enforce_openai_sdk_contract=openai_sdk_request,
    )


@response_routes_router.get("/opportunistic/admission")
async def opportunistic_admission(
    request: Request,
    model: str | None = None,
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> Response:
    denial = await opportunistic_admission_denial(request, context, api_key, model=model)
    if denial is not None:
        return denial
    return JSONResponse({"admitted": True})


@response_routes_ws_router.websocket("/responses")
async def responses_websocket(
    websocket: WebSocket,
    context: ProxyContext = Depends(get_proxy_websocket_context),
) -> None:
    api_key, denial = await validate_proxy_websocket_request(websocket)
    if denial is not None:
        await websocket.send_denial_response(denial)
        return
    try:
        await check_member_gate(api_key, model=None)
    except (TeamMemberOverCapError, TeamMemberSuspendedError) as exc:
        await websocket.send_denial_response(
            JSONResponse(
                status_code=exc.status_code,
                content=openai_error(exc.code, exc.message, error_type=exc.error_type),
                headers=exc.headers if isinstance(exc, TeamMemberOverCapError) else None,
            )
        )
        return
    turn_state = proxy_affinity_module.ensure_downstream_turn_state(websocket.headers)
    await websocket.accept(headers=proxy_affinity_module.build_downstream_turn_state_accept_headers(turn_state))
    forwarded_headers = dict(websocket.headers)
    forwarded_headers.setdefault("x-codex-turn-state", turn_state)
    await context.service.proxy_responses_websocket(
        websocket,
        forwarded_headers,
        codex_session_affinity=True,
        openai_cache_affinity=True,
        api_key=api_key,
    )


@response_routes_v1_router.post(
    "/responses",
    response_model=OpenAIResponseResult,
    responses={
        200: {
            "content": {
                "text/event-stream": {
                    "schema": {"type": "string"},
                }
            }
        }
    },
)
async def v1_responses(
    request: Request,
    payload: V1ResponsesRequest = Body(...),
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> Response:
    try:
        responses_payload = payload.to_responses_request()
        enforce_strict_text_format(responses_payload)
        enforce_strict_function_tools_format(responses_payload.tools)
    except ClientPayloadError as exc:
        error = openai_client_payload_error(exc)
        return logged_error_json_response(request, 400, error)
    except ValidationError as exc:
        error = openai_validation_error(exc)
        return logged_error_json_response(request, 400, error)
    if responses_payload.stream:
        return await stream_responses(
            request,
            responses_payload,
            context,
            api_key,
            codex_session_affinity=False,
            openai_cache_affinity=True,
            prefer_http_bridge=True,
        )
    return await collect_responses(
        request,
        responses_payload,
        context,
        api_key,
        codex_session_affinity=False,
        openai_cache_affinity=True,
        prefer_http_bridge=True,
    )


@response_routes_internal_router.post(
    "/responses",
    include_in_schema=False,
    responses={
        200: {
            "content": {
                "text/event-stream": {
                    "schema": {"type": "string"},
                }
            }
        }
    },
)
async def internal_bridge_responses(
    request: Request,
    payload: ResponsesRequest = Body(...),
    context: ProxyContext = Depends(get_proxy_context),
) -> Response:
    forwarded_request_context, internal_error = parse_forwarded_request(
        request.headers,
        payload=payload,
        current_instance=get_settings().http_responses_session_bridge_instance_id,
    )
    if internal_error is not None or forwarded_request_context is None:
        assert internal_error is not None
        return logged_error_json_response(request, internal_error.status_code, internal_error.payload)
    forward = forwarded_request_context.context
    FACTORY_ATTRIBUTION.set(forward.factory_attribution)
    api_key, auth_error = await validate_internal_bridge_api_key(request)
    if auth_error is not None:
        return auth_error
    skip_limit_enforcement = api_key is None or forward.reservation is not None
    forwarded_headers = strip_internal_bridge_headers(request.headers)
    return await stream_responses(
        request,
        payload,
        context,
        api_key,
        codex_session_affinity=forward.codex_session_affinity,
        openai_cache_affinity=True,
        prefer_http_bridge=True,
        skip_limit_enforcement=skip_limit_enforcement,
        api_key_reservation_override=forward.reservation,
        include_rate_limit_headers=False,
        forwarded_request=True,
        forwarded_identity=forward.identity,
        forwarded_headers=forwarded_headers,
        forwarded_downstream_turn_state=forward.downstream_turn_state,
        forwarded_affinity_kind=forward.original_affinity_kind,
        forwarded_affinity_key=forward.original_affinity_key,
        enforce_openai_sdk_contract=False,
    )


@response_routes_v1_ws_router.websocket("/responses")
async def v1_responses_websocket(
    websocket: WebSocket,
    context: ProxyContext = Depends(get_proxy_websocket_context),
) -> None:
    api_key, denial = await validate_proxy_websocket_request(websocket)
    if denial is not None:
        await websocket.send_denial_response(denial)
        return
    try:
        await check_member_gate(api_key, model=None)
    except (TeamMemberOverCapError, TeamMemberSuspendedError) as exc:
        await websocket.send_denial_response(
            JSONResponse(
                status_code=exc.status_code,
                content=openai_error(exc.code, exc.message, error_type=exc.error_type),
                headers=exc.headers if isinstance(exc, TeamMemberOverCapError) else None,
            )
        )
        return
    turn_state = proxy_affinity_module.ensure_downstream_turn_state(websocket.headers)
    await websocket.accept(headers=proxy_affinity_module.build_downstream_turn_state_accept_headers(turn_state))
    forwarded_headers = dict(websocket.headers)
    forwarded_headers.setdefault("x-codex-turn-state", turn_state)
    await context.service.proxy_responses_websocket(
        websocket,
        forwarded_headers,
        codex_session_affinity=False,
        openai_cache_affinity=True,
        api_key=api_key,
    )
