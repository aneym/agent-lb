from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Request, Response, Security
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError

from app.core.auth.dependencies import validate_proxy_api_key
from app.core.clients.proxy import ProxyResponseError
from app.core.config.settings import get_settings
from app.core.openai.chat_requests import ChatCompletionsRequest
from app.core.openai.chat_responses import (
    ChatCompletion,
    ChatCompletionResult,
    collect_chat_completion,
    stream_chat_chunks,
)
from app.core.openai.exceptions import ClientPayloadError
from app.core.openai.models import OpenAIErrorEnvelope as OpenAIErrorEnvelopeModel
from app.core.providers.openrouter import is_deepseek_model
from app.core.utils.sse import inject_sse_keepalives
from app.dependencies import ProxyContext, get_proxy_context
from app.modules.api_keys.service_data import ApiKeyData
from app.modules.proxy.api_key_usage import estimate_api_key_request_usage
from app.modules.proxy.constants import (
    CHAT_COMPLETIONS_STARTUP_ERROR_PROBE_SECONDS,
    CURSOR_CHAT_COMPLETIONS_STARTUP_ERROR_PROBE_SECONDS,
    UNAVAILABLE_SELECTION_ERROR_CODES,
)
from app.modules.proxy.cursor_streams import (
    apply_cursor_usage_fallback,
    cursor_context_limit_usage_completion,
    cursor_context_limit_usage_stream,
    is_context_length_startup_error,
    is_cursor_compat_client,
    stream_with_cursor_usage_fallback,
)
from app.modules.proxy.request_admission import enforce_request_limits, opportunistic_admission_denial
from app.modules.proxy.request_policy import (
    apply_api_key_enforcement,
    enforce_strict_function_tools_format,
    enforce_strict_text_format,
    openai_client_payload_error,
    openai_validation_error,
    validate_model_access,
)
from app.modules.proxy.reservations import effective_model_for_api_key, release_reservation
from app.modules.proxy.response_errors import logged_error_json_response
from app.modules.proxy.stream_iteration import prepend_first
from app.modules.proxy.stream_transport import (
    probe_chat_stream_startup_error,
    stream_proxy_errors_as_response_failed,
    stream_startup_error_response,
)

chat_routes_v1_router = APIRouter(prefix="/v1")


@chat_routes_v1_router.post(
    "/chat/completions",
    response_model=ChatCompletionResult,
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
async def v1_chat_completions(
    request: Request,
    payload: ChatCompletionsRequest = Body(...),
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> Response:
    effective_model = effective_model_for_api_key(api_key, payload.model)
    if is_deepseek_model(effective_model):
        from app.modules.proxy.openrouter_api import chat_response

        return await chat_response(request, effective_model, payload.stream, api_key)
    cursor_compat_client = is_cursor_compat_client(request, api_key)
    effective_model = effective_model_for_api_key(api_key, payload.model)
    validate_model_access(api_key, effective_model)

    rate_limit_headers = await context.service.rate_limit_headers()
    try:
        responses_shaped_payload = not payload.messages and payload.input is not None
        if not responses_shaped_payload:
            enforce_strict_function_tools_format(
                payload.tools,
                param_template="tools[{index}].function.parameters",
                nested=True,
            )
        responses_payload = payload.to_responses_request()
        enforce_strict_text_format(responses_payload)
        if responses_shaped_payload:
            enforce_strict_function_tools_format(responses_payload.tools)
    except ClientPayloadError as exc:
        error = openai_client_payload_error(exc)
        return logged_error_json_response(request, 400, error, headers=rate_limit_headers)
    except ValidationError as exc:
        error = openai_validation_error(exc)
        return logged_error_json_response(request, 400, error, headers=rate_limit_headers)
    apply_api_key_enforcement(responses_payload, api_key)
    admission_denial = await opportunistic_admission_denial(request, context, api_key, model=responses_payload.model)
    if admission_denial is not None:
        return admission_denial
    reservation = await enforce_request_limits(
        api_key,
        request_model=responses_payload.model,
        request_service_tier=responses_payload.service_tier,
        request_usage_budget=estimate_api_key_request_usage(responses_payload),
    )
    responses_payload.stream = True
    stream = context.service.stream_responses(
        responses_payload,
        request.headers,
        codex_session_affinity=False,
        propagate_http_errors=True,
        openai_cache_affinity=True,
        api_key=api_key,
        api_key_reservation=reservation,
        suppress_text_done_events=True,
        retry_protocol_only_failures=True,
    )
    startup_probe_timeout = (
        CURSOR_CHAT_COMPLETIONS_STARTUP_ERROR_PROBE_SECONDS
        if cursor_compat_client
        else CHAT_COMPLETIONS_STARTUP_ERROR_PROBE_SECONDS
    )
    stream, startup_error = await probe_chat_stream_startup_error(stream, timeout_seconds=startup_probe_timeout)
    if startup_error is not None:
        if cursor_compat_client and is_context_length_startup_error(startup_error):
            await release_reservation(reservation)
            if payload.stream:
                return cursor_context_limit_usage_stream(
                    payload,
                    headers=rate_limit_headers,
                )
            return cursor_context_limit_usage_completion(
                payload,
                headers=rate_limit_headers,
            )
        return stream_startup_error_response(request, startup_error, headers=rate_limit_headers)
    if payload.stream:
        stream_options = payload.stream_options
        include_usage = cursor_compat_client or bool(stream_options and stream_options.include_usage)
        chat_stream = stream_chat_chunks(
            stream_proxy_errors_as_response_failed(stream),
            model=responses_payload.model,
            include_usage=include_usage,
        )
        if cursor_compat_client:
            chat_stream = stream_with_cursor_usage_fallback(chat_stream, payload)
        return StreamingResponse(
            inject_sse_keepalives(
                chat_stream,
                get_settings().sse_keepalive_interval_seconds,
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", **rate_limit_headers},
        )

    try:
        first = await stream.__anext__()
    except StopAsyncIteration:
        first = None
    except ProxyResponseError as exc:
        return logged_error_json_response(request, exc.status_code, exc.payload, headers=rate_limit_headers)

    stream_with_first = prepend_first(first, stream)
    result = await collect_chat_completion(stream_with_first, model=responses_payload.model)
    if isinstance(result, OpenAIErrorEnvelopeModel):
        error = result.error
        code = error.code if error else None
        status_code = 503 if code in UNAVAILABLE_SELECTION_ERROR_CODES else 502
        return logged_error_json_response(
            request,
            status_code,
            content=result.model_dump(mode="json", exclude_none=True),
            headers=rate_limit_headers,
        )
    if cursor_compat_client and isinstance(result, ChatCompletion):
        apply_cursor_usage_fallback(result, payload, source="non_stream")
    return JSONResponse(
        content=result.model_dump(mode="json", exclude_none=True),
        status_code=200,
        headers=rate_limit_headers,
    )
