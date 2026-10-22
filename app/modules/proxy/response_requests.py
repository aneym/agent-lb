from __future__ import annotations

from collections.abc import Mapping

from fastapi import Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from app.core.clients.proxy import ProxyResponseError
from app.core.config.settings import get_settings
from app.core.identity import RequestIdentity
from app.core.openai.models import OpenAIResponsePayload
from app.core.openai.requests import ResponsesReasoning, ResponsesRequest
from app.core.utils.request_id import get_request_id
from app.core.utils.sse import CODEX_KEEPALIVE_FRAME, SSE_KEEPALIVE_FRAME, inject_sse_keepalives
from app.dependencies import ProxyContext
from app.modules.api_keys.service_data import ApiKeyData, ApiKeyUsageReservationData
from app.modules.proxy import affinity as proxy_affinity_module
from app.modules.proxy import service as proxy_service_module
from app.modules.proxy.api_key_usage import estimate_api_key_request_usage
from app.modules.proxy.constants import HTTP_BRIDGE_STARTUP_ERROR_PROBE_SECONDS, STREAM_STARTUP_ERROR_PROBE_SECONDS
from app.modules.proxy.public_streams import normalize_public_responses_stream
from app.modules.proxy.request_admission import enforce_request_limits, opportunistic_admission_denial
from app.modules.proxy.request_policy import apply_api_key_enforcement, validate_model_access
from app.modules.proxy.reservations import release_reservation
from app.modules.proxy.response_collection import collect_responses_payload
from app.modules.proxy.response_errors import (
    error_envelope_from_response,
    logged_error_json_response,
    mask_previous_response_not_found_error,
    parse_error_envelope,
)
from app.modules.proxy.stream_iteration import prepend_initial_sse_heartbeat
from app.modules.proxy.stream_transport import (
    probe_stream_startup_error,
    stream_response_error_events,
    stream_startup_error_response,
)


async def stream_responses(
    request: Request,
    payload: ResponsesRequest,
    context: ProxyContext,
    api_key: ApiKeyData | None,
    *,
    codex_session_affinity: bool = False,
    openai_cache_affinity: bool = False,
    suppress_text_done_events: bool = False,
    prefer_http_bridge: bool = False,
    skip_limit_enforcement: bool = False,
    api_key_reservation_override: ApiKeyUsageReservationData | None = None,
    include_rate_limit_headers: bool = True,
    forwarded_request: bool = False,
    forwarded_identity: RequestIdentity | None = None,
    forwarded_headers: Mapping[str, str] | None = None,
    forwarded_downstream_turn_state: str | None = None,
    forwarded_affinity_kind: str | None = None,
    forwarded_affinity_key: str | None = None,
    enforce_openai_sdk_contract: bool = True,
    client_session_id: str | None = None,
    locked_model: str | None = None,
    locked_reasoning_effort: str | None = None,
    locked_service_tier: str | None = None,
) -> Response:
    apply_api_key_enforcement(payload, api_key)
    if locked_model is not None:
        payload.model = locked_model
    if locked_reasoning_effort is not None:
        payload.reasoning = ResponsesReasoning(effort=locked_reasoning_effort, summary="auto")
    if locked_service_tier is not None:
        payload.service_tier = locked_service_tier
    validate_model_access(api_key, payload.model)
    admission_denial = await opportunistic_admission_denial(request, context, api_key, model=payload.model)
    if admission_denial is not None:
        return admission_denial
    owns_reservation = api_key_reservation_override is None
    reservation = (
        api_key_reservation_override
        if skip_limit_enforcement
        else await enforce_request_limits(
            api_key,
            request_model=payload.model,
            request_service_tier=payload.service_tier,
            request_usage_budget=estimate_api_key_request_usage(payload),
        )
    )

    rate_limit_headers = await context.service.rate_limit_headers() if include_rate_limit_headers else {}
    bridge_active = prefer_http_bridge and proxy_service_module.get_settings().http_responses_session_bridge_enabled
    effective_headers = forwarded_headers or request.headers
    downstream_turn_state = (
        forwarded_downstream_turn_state
        if bridge_active and forwarded_downstream_turn_state is not None
        else proxy_affinity_module.ensure_http_downstream_turn_state(effective_headers)
        if bridge_active
        else None
    )
    turn_state_headers = (
        proxy_affinity_module.build_downstream_turn_state_response_headers(downstream_turn_state)
        if downstream_turn_state is not None
        else {}
    )
    payload.stream = True
    if prefer_http_bridge:
        stream = context.service.stream_http_responses(
            payload,
            effective_headers,
            codex_session_affinity=codex_session_affinity,
            propagate_http_errors=True,
            openai_cache_affinity=openai_cache_affinity,
            api_key=api_key,
            api_key_reservation=reservation,
            suppress_text_done_events=suppress_text_done_events,
            downstream_turn_state=downstream_turn_state,
            forwarded_request=forwarded_request,
            forwarded_identity=forwarded_identity,
            forwarded_affinity_kind=forwarded_affinity_kind,
            forwarded_affinity_key=forwarded_affinity_key,
            retry_protocol_only_failures=enforce_openai_sdk_contract,
            client_session_id=client_session_id,
        )
    else:
        stream = context.service.stream_responses(
            payload,
            effective_headers,
            codex_session_affinity=codex_session_affinity,
            propagate_http_errors=True,
            openai_cache_affinity=openai_cache_affinity,
            api_key=api_key,
            api_key_reservation=reservation,
            suppress_text_done_events=suppress_text_done_events,
            retry_protocol_only_failures=enforce_openai_sdk_contract,
        )
    stream, startup_error = await probe_stream_startup_error(
        stream,
        convert_event_errors=bridge_active,
        timeout_seconds=(
            HTTP_BRIDGE_STARTUP_ERROR_PROBE_SECONDS if prefer_http_bridge else STREAM_STARTUP_ERROR_PROBE_SECONDS
        ),
    )
    if startup_error is not None:
        if owns_reservation:
            await release_reservation(reservation)
        return stream_startup_error_response(
            request,
            startup_error,
            headers=rate_limit_headers,
        )
    stream = normalize_public_responses_stream(
        stream_response_error_events(
            stream,
            owns_reservation=owns_reservation,
            reservation=reservation,
        ),
        enforce_openai_sdk_contract=enforce_openai_sdk_contract,
    )
    keepalive_frame = CODEX_KEEPALIVE_FRAME if not enforce_openai_sdk_contract else SSE_KEEPALIVE_FRAME
    if not enforce_openai_sdk_contract:
        stream = prepend_initial_sse_heartbeat(
            stream,
            keepalive_frame,
            request_id=get_request_id(),
            route_family="responses",
        )
    return StreamingResponse(
        inject_sse_keepalives(
            stream,
            get_settings().sse_keepalive_interval_seconds,
            keepalive_frame=keepalive_frame,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            **turn_state_headers,
            **rate_limit_headers,
        },
    )


def strip_internal_bridge_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {key: value for key, value in headers.items() if not key.lower().startswith("x-codex-bridge-")}


async def collect_responses(
    request: Request,
    payload: ResponsesRequest,
    context: ProxyContext,
    api_key: ApiKeyData | None,
    *,
    codex_session_affinity: bool = False,
    openai_cache_affinity: bool = False,
    suppress_text_done_events: bool = False,
    prefer_http_bridge: bool = False,
) -> Response:
    apply_api_key_enforcement(payload, api_key)
    validate_model_access(api_key, payload.model)
    admission_denial = await opportunistic_admission_denial(request, context, api_key, model=payload.model)
    if admission_denial is not None:
        return admission_denial
    reservation = await enforce_request_limits(
        api_key,
        request_model=payload.model,
        request_service_tier=payload.service_tier,
        request_usage_budget=estimate_api_key_request_usage(payload),
    )

    rate_limit_headers = await context.service.rate_limit_headers()
    bridge_active = prefer_http_bridge and proxy_service_module.get_settings().http_responses_session_bridge_enabled
    downstream_turn_state = (
        proxy_affinity_module.ensure_http_downstream_turn_state(request.headers) if bridge_active else None
    )
    turn_state_headers = (
        proxy_affinity_module.build_downstream_turn_state_response_headers(downstream_turn_state)
        if downstream_turn_state is not None
        else {}
    )
    payload.stream = True
    if prefer_http_bridge:
        stream = context.service.stream_http_responses(
            payload,
            request.headers,
            codex_session_affinity=codex_session_affinity,
            propagate_http_errors=True,
            openai_cache_affinity=openai_cache_affinity,
            api_key=api_key,
            api_key_reservation=reservation,
            suppress_text_done_events=suppress_text_done_events,
            downstream_turn_state=downstream_turn_state,
        )
    else:
        stream = context.service.stream_responses(
            payload,
            request.headers,
            codex_session_affinity=codex_session_affinity,
            propagate_http_errors=True,
            openai_cache_affinity=openai_cache_affinity,
            api_key=api_key,
            api_key_reservation=reservation,
            suppress_text_done_events=suppress_text_done_events,
        )
    try:
        response_payload = await collect_responses_payload(stream)
    except ProxyResponseError as exc:
        await release_reservation(reservation)
        error = parse_error_envelope(exc.payload)
        status_code, error = mask_previous_response_not_found_error(error, default_status=exc.status_code)
        return logged_error_json_response(
            request,
            status_code,
            error.model_dump(mode="json", exclude_none=True),
            headers=rate_limit_headers,
        )
    if isinstance(response_payload, OpenAIResponsePayload):
        if response_payload.status == "failed":
            error_payload = error_envelope_from_response(response_payload.error)
            status_code, error_payload = mask_previous_response_not_found_error(error_payload)
            return logged_error_json_response(
                request,
                status_code,
                error_payload.model_dump(mode="json", exclude_none=True),
                headers={**turn_state_headers, **rate_limit_headers},
            )
        return JSONResponse(
            content=response_payload.model_dump(mode="json", exclude_none=True),
            headers={**turn_state_headers, **rate_limit_headers},
        )
    status_code, response_payload = mask_previous_response_not_found_error(response_payload)
    return logged_error_json_response(
        request,
        status_code,
        response_payload.model_dump(mode="json", exclude_none=True),
        headers={**turn_state_headers, **rate_limit_headers},
    )
