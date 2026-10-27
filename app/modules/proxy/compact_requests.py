from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Request, Security
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.core.auth.dependencies import validate_proxy_api_key
from app.core.clients.proxy import ProxyResponseError
from app.core.openai.exceptions import ClientPayloadError
from app.core.openai.models import CompactResponseResult, OpenAIError
from app.core.openai.models import OpenAIErrorEnvelope as OpenAIErrorEnvelopeModel
from app.core.openai.requests import ResponsesCompactRequest
from app.core.openai.v1_requests import V1ResponsesCompactRequest
from app.dependencies import ProxyContext, get_proxy_context
from app.modules.api_keys.service_data import ApiKeyData
from app.modules.proxy.api_key_usage import estimate_api_key_request_usage
from app.modules.proxy.request_admission import enforce_request_limits, opportunistic_admission_denial
from app.modules.proxy.request_policy import (
    apply_api_key_enforcement,
    openai_client_payload_error,
    openai_validation_error,
    validate_model_access,
)
from app.modules.proxy.reservations import compact_request_service_tier, release_reservation
from app.modules.proxy.response_errors import (
    logged_error_json_response,
    mask_previous_response_not_found_error,
    parse_error_envelope,
)

compact_requests_router = APIRouter(prefix="/backend-api/codex")


compact_requests_v1_router = APIRouter(prefix="/v1")


@compact_requests_router.post("/responses/compact", response_model=CompactResponseResult)
async def responses_compact(
    request: Request,
    payload: ResponsesCompactRequest = Body(...),
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> JSONResponse:
    return await compact_responses(
        request, payload, context, api_key, codex_session_affinity=True, openai_cache_affinity=True
    )


@compact_requests_v1_router.post("/responses/compact", response_model=CompactResponseResult)
async def v1_responses_compact(
    request: Request,
    payload: V1ResponsesCompactRequest = Body(...),
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> JSONResponse:
    try:
        compact_payload = payload.to_compact_request()
    except ClientPayloadError as exc:
        error = openai_client_payload_error(exc)
        return logged_error_json_response(request, 400, error)
    except ValidationError as exc:
        error = openai_validation_error(exc)
        return logged_error_json_response(request, 400, error)
    return await compact_responses(
        request,
        compact_payload,
        context,
        api_key,
        codex_session_affinity=False,
        openai_cache_affinity=True,
    )


async def compact_responses(
    request: Request,
    payload: ResponsesCompactRequest,
    context: ProxyContext,
    api_key: ApiKeyData | None,
    codex_session_affinity: bool = False,
    openai_cache_affinity: bool = False,
) -> JSONResponse:
    apply_api_key_enforcement(payload, api_key)
    validate_model_access(api_key, payload.model)
    admission_denial = await opportunistic_admission_denial(
        request,
        context,
        api_key,
        model=payload.model,
        lease_kind="response_create",
    )
    if admission_denial is not None:
        return admission_denial
    reservation = await enforce_request_limits(
        api_key,
        request_model=payload.model,
        request_service_tier=compact_request_service_tier(payload),
        request_usage_budget=estimate_api_key_request_usage(payload),
    )

    rate_limit_headers = await context.service.rate_limit_headers()
    try:
        result = await context.service.compact_responses(
            payload,
            request.headers,
            codex_session_affinity=codex_session_affinity,
            openai_cache_affinity=openai_cache_affinity,
            api_key=api_key,
            api_key_reservation=reservation,
        )
    except NotImplementedError:
        error = OpenAIErrorEnvelopeModel(
            error=OpenAIError(
                message="responses/compact is not implemented",
                type="server_error",
                code="not_implemented",
            )
        )
        return logged_error_json_response(
            request,
            501,
            error.model_dump(mode="json", exclude_none=True),
            headers=rate_limit_headers,
        )
    except ProxyResponseError as exc:
        error = parse_error_envelope(exc.payload)
        status_code, error = mask_previous_response_not_found_error(error, default_status=exc.status_code)
        return logged_error_json_response(
            request,
            status_code,
            error.model_dump(mode="json", exclude_none=True),
            headers=rate_limit_headers,
        )
    finally:
        await release_reservation(reservation)
    return JSONResponse(
        content=result.model_dump(mode="json", exclude_none=True),
        headers=rate_limit_headers,
    )
