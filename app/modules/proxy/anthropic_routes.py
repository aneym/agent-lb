from __future__ import annotations

import re
from collections.abc import AsyncIterator
from hashlib import sha256

from fastapi import APIRouter, Body, Depends, Request, Response, Security
from fastapi.responses import JSONResponse, StreamingResponse

from app.core.anthropic.models import AnthropicMessageRequest
from app.core.auth.dependencies import validate_proxy_api_key
from app.core.config.settings import get_settings
from app.core.exceptions import ProxyAuthError, ProxyModelNotAllowed, ProxyRateLimitError
from app.core.providers import get_anthropic_compat_profile
from app.core.types import JsonValue
from app.core.upload_admission import is_headless_claude_user_agent
from app.core.utils.client_session import get_caller_seat, get_client_session_id
from app.core.utils.sse import inject_sse_keepalives
from app.dependencies import AnthropicProxyContext, ProxyContext, get_anthropic_proxy_context, get_proxy_context
from app.modules.api_keys.service_data import ApiKeyData
from app.modules.proxy.anthropic_errors import (
    anthropic_error_response,
    anthropic_proxy_error_response,
    anthropic_stream_error_guard,
    collect_anthropic_body,
    estimate_anthropic_request_usage,
)
from app.modules.proxy.anthropic_proxy_error import AnthropicProxyError
from app.modules.proxy.api_dependencies import proxy_dependencies
from app.modules.proxy.classifier_route import classifier_payload
from app.modules.proxy.claude_codex_bridge import (
    CCGPT_REASONING_EFFORT,
    CCGPT_SERVICE_TIER,
    anthropic_error_from_response,
    anthropic_status_for_error,
    claude_to_responses,
    collect_claude_message,
    estimate_claude_input_tokens,
    format_claude_events,
    responses_to_claude_events,
    split_startup_error,
)
from app.modules.proxy.constants import UNTAGGED_HEADLESS_OPUS_MESSAGE, logger
from app.modules.proxy.model_families import default_ccgpt_model, resolve_ccgpt_model
from app.modules.proxy.request_admission import enforce_request_limits
from app.modules.proxy.request_policy import validate_model_access
from app.modules.proxy.reservations import release_reservation
from app.modules.proxy.response_requests import stream_responses
from app.modules.proxy.server_tool_route import carries_server_tools, server_tool_claude_model

anthropic_routes_v1_router = APIRouter(prefix="/v1")


anthropic_routes_frame_db_router = APIRouter(prefix="")


def untagged_headless_opus_refusal(request: Request, model: str | None) -> Response | None:
    if not re.match(r"^claude-opus(?:-|$)", model or "", re.IGNORECASE):
        return None
    if not is_headless_claude_user_agent(request.headers.get("user-agent")):
        return None
    if get_caller_seat() is not None:
        return None
    if (request.headers.get("x-agent-lb-priority") or "").strip().lower() == "high":
        return None
    session = (get_client_session_id() or "-")[:80]
    if not get_settings().refuse_untagged_headless_opus:
        logger.info("untagged_headless_opus_would_refuse model=%s session=%s", model, session)
        return None
    logger.info("untagged_headless_opus_refused model=%s session=%s", model, session)
    return anthropic_error_response(403, "permission_error", UNTAGGED_HEADLESS_OPUS_MESSAGE)


@anthropic_routes_v1_router.post(
    "/messages",
    response_model=None,
    responses={
        200: {
            "content": {
                "text/event-stream": {
                    "schema": {"type": "string"},
                },
                "application/json": {
                    "schema": {"type": "object"},
                },
            }
        }
    },
)
async def v1_messages(
    request: Request,
    payload: AnthropicMessageRequest = Body(...),
    context: AnthropicProxyContext = Depends(get_anthropic_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> Response:
    payload = classifier_payload(payload, request.headers)
    ccgpt_model = resolve_ccgpt_model(payload.model)
    if ccgpt_model and (claude_model := server_tool_claude_model(payload)):
        payload = payload.model_copy(update={"model": claude_model})
    elif ccgpt_model:
        return await ccgpt_messages_response(
            request, payload, get_proxy_context(request), api_key, ccgpt_model[1], ccgpt_model[0]
        )
    from app.modules.proxy import stand_in

    intended_model = payload.model
    intent = request.headers.get("x-agent-lb-intent")
    lane = request.headers.get("x-agent-lb-lane")
    review_lane = stand_in.is_review_lane(lane)
    main_thread = "x-claude-code-agent-id" not in request.headers
    swap_candidate = (
        main_thread
        and bool(intent)
        and bool(re.match(r"^claude-(?:opus|sonnet)(?:-|$)", intended_model))
        and stand_in.stand_in_model(intent) is not None
    )
    eligible = swap_candidate and not review_lane
    session_id = proxy_dependencies.anthropic_request_session_id(payload, request.headers)
    resolved_request = await context.service.resolve_message_request(payload)
    payload = resolved_request.payload
    validate_model_access(api_key, payload.model)
    refusal = untagged_headless_opus_refusal(request, payload.model)
    if refusal is not None:
        return refusal
    try:
        request_usage_budget = (
            estimate_anthropic_request_usage(payload, raw_body=await request.body()) if api_key is not None else None
        )
        reservation = await enforce_request_limits(
            api_key,
            request_model=payload.model,
            request_service_tier=None,
            request_usage_budget=request_usage_budget,
        )
    except ProxyRateLimitError as exc:
        return anthropic_error_response(429, "rate_limit_error", str(exc))
    except ProxyModelNotAllowed as exc:
        return anthropic_error_response(403, "permission_error", str(exc), code=exc.code)
    except ProxyAuthError as exc:
        return anthropic_error_response(401, "authentication_error", str(exc))

    async def failure_response(exc: AnthropicProxyError) -> Response:
        await release_reservation(reservation)
        alias = stand_in.stand_in_model(intent) if swap_candidate else None
        resolved = resolve_ccgpt_model(alias)
        if resolved is None or not (
            exc.status_code == 429 or exc.status_code >= 500 or exc.code.startswith("no_available_")
        ):
            return anthropic_proxy_error_response(exc)
        if review_lane:
            safe_lane = re.sub(r"[^A-Za-z0-9._:/-]", "?", lane or "")[:80]
            logger.info("stand_in_refused_review_lane lane=%s", safe_lane)
            return anthropic_proxy_error_response(exc)
        locked_model, alias_effort = resolved
        response = await ccgpt_messages_response(
            request, payload, get_proxy_context(request), api_key, alias_effort, locked_model
        )
        if response.status_code < 400 and session_id:
            try:
                stand_in.record_failure(
                    session_id,
                    lane=request.headers.get("x-agent-lb-lane"),
                    intent=intent or "",
                    intended=intended_model,
                    running=locked_model,
                    effort=alias_effort,
                    reason=f"{exc.code}: {exc.message}",
                    retry_at=exc.retry_at,
                )
            except Exception:
                logger.warning("Unable to record stand-in failure")
        response.headers["x-agent-lb-standing-in"] = f"{locked_model} for {intended_model}"
        return response

    try:
        stream = await context.service.stream_messages(
            payload,
            request.headers,
            api_key=api_key,
            api_key_reservation=reservation,
        )
    except AnthropicProxyError as exc:
        return await failure_response(exc)

    if not payload.stream:
        try:
            body = await collect_anthropic_body(stream.body)
        except AnthropicProxyError as exc:
            return await failure_response(exc)
        if eligible and session_id:
            try:
                stand_in.record_success(session_id)
            except Exception:
                logger.warning("Unable to record stand-in return")
        return Response(content=body, media_type=stream.media_type)

    stream_body = stream.body
    if swap_candidate:
        iterator = stream.body.__aiter__()
        try:
            first_chunk = await anext(iterator)
        except StopAsyncIteration:
            first_chunk = None
        except AnthropicProxyError as exc:
            return await failure_response(exc)

        async def prefetched_body() -> AsyncIterator[bytes]:
            if first_chunk is not None:
                yield first_chunk
            async for chunk in iterator:
                yield chunk

        stream_body = prefetched_body()
        if eligible and first_chunk is not None and session_id:
            try:
                stand_in.record_success(session_id)
            except Exception:
                logger.warning("Unable to record stand-in return")

    return StreamingResponse(
        inject_sse_keepalives(
            anthropic_stream_error_guard(
                stream_body,
                streaming=bool(payload.stream),
                api_key_reservation=reservation,
            ),
            get_settings().sse_keepalive_interval_seconds,
        ),
        media_type=stream.media_type,
    )


@anthropic_routes_frame_db_router.api_route(
    "/api/frame/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"], response_model=None
)
async def anthropic_frame(
    request: Request,
    context: AnthropicProxyContext = Depends(get_anthropic_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> Response:
    raw_path = request.scope["raw_path"].decode("ascii")
    query = request.scope["query_string"].decode("ascii")
    try:
        result = await context.service.frame_request(
            request.method,
            raw_path + (f"?{query}" if query else ""),
            await request.body(),
            request.headers,
            api_key=api_key,
        )
    except AnthropicProxyError as exc:
        return anthropic_proxy_error_response(exc)
    return Response(content=result.body, status_code=result.status_code, headers=result.headers)


@anthropic_routes_v1_router.post("/messages/count_tokens", response_model=None)
async def v1_messages_count_tokens(
    request: Request,
    payload: dict[str, JsonValue] = Body(...),
    context: AnthropicProxyContext = Depends(get_anthropic_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> Response:
    model = payload.get("model")
    if not isinstance(model, str) or not model.strip():
        return anthropic_error_response(400, "invalid_request_error", "model is required")
    ccgpt_model = resolve_ccgpt_model(model)
    if ccgpt_model is not None:
        validate_model_access(api_key, ccgpt_model[0])
        return JSONResponse(content={"input_tokens": estimate_claude_input_tokens(payload)})
    resolved_request = await context.service.resolve_count_tokens_request(payload, model=model)
    payload = dict(resolved_request.body)
    model = resolved_request.model
    validate_model_access(api_key, model)
    profile = get_anthropic_compat_profile(resolved_request.provider_name)
    if not profile.supports_upstream_count_tokens:
        return JSONResponse(content={"input_tokens": estimate_claude_input_tokens(payload)})

    try:
        result = await context.service.count_tokens(payload, request.headers, model=model)
    except AnthropicProxyError as exc:
        return anthropic_proxy_error_response(exc)
    return Response(content=result.body, status_code=result.status_code, media_type=result.media_type)


@anthropic_routes_v1_router.post("/ccgpt/messages", response_model=None)
async def v1_ccgpt_messages(
    request: Request,
    payload: AnthropicMessageRequest = Body(...),
    context: ProxyContext = Depends(get_proxy_context),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> Response:
    if carries_server_tools(payload):
        return await v1_messages(request, payload, get_anthropic_proxy_context(request), api_key)
    resolved = resolve_ccgpt_model(payload.model)
    if resolved is None and not payload.model.startswith("claude-"):
        return anthropic_error_response(
            400, "invalid_request_error", f"model {payload.model} is not a served GPT model"
        )
    locked_model, alias_effort = resolved or default_ccgpt_model()
    return await ccgpt_messages_response(
        request, payload, context, api_key, alias_effort=alias_effort, locked_model=locked_model
    )


async def ccgpt_messages_response(
    request: Request,
    payload: AnthropicMessageRequest,
    context: ProxyContext,
    api_key: ApiKeyData | None,
    alias_effort: str | None = None,
    locked_model: str | None = None,
) -> Response:
    thread = (payload.model_extra or {}).get("thread")
    if thread is not None:
        return anthropic_error_response(
            400,
            "invalid_request_error",
            "thread: threads unsupported by the ccgpt compatibility route",
            details={"error_code": "thread_unsupported_request"},
        )
    if carries_server_tools(payload):
        return anthropic_error_response(
            400,
            "invalid_request_error",
            "Anthropic-defined tools are unsupported by the ccgpt compatibility route",
        )
    locked_model = locked_model or default_ccgpt_model()[0]
    client_session_id = proxy_dependencies.anthropic_request_session_id(payload, request.headers)
    responses_payload = claude_to_responses(payload, locked_model)
    responses_payload.model = locked_model
    if client_session_id:
        agent_id = request.headers.get("x-claude-code-agent-id", "").strip()
        if agent_id:
            conversation = f"agent:{agent_id}"
        else:
            first = payload.messages[0].content if payload.messages else ""
            conversation = first if isinstance(first, str) else "\n".join(part.text or part.type for part in first)
        anchor = "\0".join((client_session_id, locked_model, conversation))
        responses_payload.prompt_cache_key = f"ccgpt-{sha256(anchor.encode()).hexdigest()[:32]}"
    forwarded_headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in {"authorization", "x-api-key", "proxy-authorization"}
        and not key.lower().startswith("anthropic-")
    }
    try:
        upstream = await stream_responses(
            request,
            responses_payload,
            context,
            api_key,
            codex_session_affinity=True,
            openai_cache_affinity=True,
            prefer_http_bridge=True,
            forwarded_headers=forwarded_headers,
            client_session_id=client_session_id,
            locked_model=locked_model,
            locked_reasoning_effort=alias_effort
            or (responses_payload.reasoning.effort if responses_payload.reasoning else CCGPT_REASONING_EFFORT),
            locked_service_tier=CCGPT_SERVICE_TIER,
        )
    except ProxyRateLimitError as exc:
        return anthropic_error_response(429, "rate_limit_error", str(exc))
    except ProxyAuthError as exc:
        return anthropic_error_response(401, "authentication_error", str(exc))

    if not isinstance(upstream, StreamingResponse):
        body = getattr(upstream, "body", b"")
        body_bytes = body if isinstance(body, bytes) else str(body).encode()
        if upstream.status_code in (401, 403):
            return anthropic_error_response(
                503,
                "api_error",
                f"No Codex account accepted the request (upstream HTTP {upstream.status_code}); retry shortly.",
            )
        error = anthropic_error_from_response(upstream.status_code, body_bytes)
        return JSONResponse(content=error, status_code=upstream.status_code)

    startup_error, events = await split_startup_error(
        responses_to_claude_events(
            upstream.body_iterator,
            model=locked_model,
            input_tokens_estimate=estimate_claude_input_tokens(payload.model_dump(mode="json", exclude_none=True)),
        )
    )
    if startup_error is not None:
        return JSONResponse(content=startup_error, status_code=anthropic_status_for_error(startup_error))
    claude_stream = format_claude_events(events)
    if not payload.stream:
        message = await collect_claude_message(claude_stream)
        return JSONResponse(content=message)
    return StreamingResponse(
        inject_sse_keepalives(claude_stream, get_settings().sse_keepalive_interval_seconds),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


@anthropic_routes_v1_router.post("/ccgpt/messages/count_tokens", response_model=None)
async def v1_ccgpt_messages_count_tokens(
    payload: dict[str, JsonValue] = Body(...),
    api_key: ApiKeyData | None = Security(validate_proxy_api_key),
) -> Response:
    validate_model_access(api_key, default_ccgpt_model()[0])
    return JSONResponse(content={"input_tokens": estimate_claude_input_tokens(payload)})
