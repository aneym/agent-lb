from __future__ import annotations

from fastapi import Request, Response
from fastapi.responses import StreamingResponse

from app.core.anthropic.models import AnthropicMessageRequest
from app.dependencies import get_anthropic_proxy_context
from app.modules.api_keys.service_data import ApiKeyData
from app.modules.proxy.anthropic_service import AnthropicProxyError


async def chat_response(
    request: Request, effective_model: str, stream_requested: bool | None, api_key: ApiKeyData | None
) -> Response:
    from app.core.errors import openai_error
    from app.modules.proxy.anthropic_errors import collect_anthropic_body, estimate_anthropic_request_usage
    from app.modules.proxy.request_admission import enforce_request_limits
    from app.modules.proxy.request_policy import validate_model_access
    from app.modules.proxy.reservations import release_reservation
    from app.modules.proxy.response_errors import logged_error_json_response

    validate_model_access(api_key, effective_model)
    raw_body = await request.json()
    raw_body["model"] = effective_model
    if stream_requested:
        raw_body["stream_options"] = {**(raw_body.get("stream_options") or {}), "include_usage": True}
    routing_payload = AnthropicMessageRequest(
        model=effective_model,
        max_tokens=raw_body.get("max_completion_tokens") or raw_body.get("max_tokens") or 4096,
        messages=[{"role": "user", "content": ""}],
        stream=stream_requested,
    )
    reservation = await enforce_request_limits(
        api_key,
        request_model=effective_model,
        request_service_tier=None,
        request_usage_budget=estimate_anthropic_request_usage(routing_payload, raw_body=await request.body()),
    )
    try:
        stream = await get_anthropic_proxy_context(request).service.stream_messages(
            routing_payload,
            request.headers,
            openai_body=raw_body,
            api_key=api_key,
            api_key_reservation=reservation,
        )
        if stream_requested:
            return StreamingResponse(stream.body, media_type=stream.media_type)
        return Response(content=await collect_anthropic_body(stream.body), media_type=stream.media_type)
    except AnthropicProxyError as exc:
        await release_reservation(reservation)
        return logged_error_json_response(request, exc.status_code, openai_error(exc.code, exc.message))
