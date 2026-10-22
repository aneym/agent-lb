from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping

from fastapi import Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError

from app.core.clients.proxy import ProxyResponseError
from app.core.openai.exceptions import ClientPayloadError
from app.core.openai.images import V1ImageResponse, V1ImagesEditsForm, V1ImagesGenerationsRequest
from app.dependencies import ProxyContext
from app.modules.api_keys.service_data import ApiKeyData
from app.modules.proxy import images_service as images_service_module
from app.modules.proxy import service as proxy_service_module
from app.modules.proxy.request_admission import enforce_request_limits
from app.modules.proxy.request_policy import openai_client_payload_error, openai_validation_error, validate_model_access
from app.modules.proxy.reservations import effective_model_for_api_key, finalize_image_reservation, release_reservation
from app.modules.proxy.response_errors import logged_error_json_response, status_for_image_error_envelope


async def prime_upstream_stream(
    request: Request,
    upstream: AsyncIterator[str],
    rate_limit_headers: Mapping[str, str],
    *,
    on_error: Callable[[], Awaitable[None]] | None = None,
) -> tuple[AsyncIterator[str] | None, Response | None]:

    iterator = upstream.__aiter__()
    try:
        first_chunk = await iterator.__anext__()
    except StopAsyncIteration:
        first_chunk = None
    except ProxyResponseError as exc:
        if on_error is not None:
            await on_error()
        return None, logged_error_json_response(
            request,
            exc.status_code,
            exc.payload,
            headers=dict(rate_limit_headers),
        )

    async def _replay() -> AsyncIterator[str]:
        if first_chunk is not None:
            yield first_chunk
        async for chunk in iterator:
            yield chunk

    return _replay(), None


async def proxy_images_generation_request(
    *,
    request: Request,
    payload: V1ImagesGenerationsRequest,
    context: ProxyContext,
    api_key: ApiKeyData | None,
) -> Response:

    settings = proxy_service_module.get_settings()
    requested_model = payload.model
    effective_model = effective_model_for_api_key(
        api_key,
        requested_model or settings.images_default_model,
    )
    if not images_service_module.is_supported_image_model(effective_model):
        return logged_error_json_response(
            request,
            400,
            images_service_module.make_invalid_request_error(
                f"Effective model '{effective_model}' is not a 'gpt-image-*' model. "
                f"This API key is pinned to '{effective_model}' which cannot be used on "
                f"/v1/images/* routes; use a key that allows gpt-image models.",
                param="model",
            ),
        )
    if effective_model != requested_model:
        payload = payload.model_copy(update={"model": effective_model})

    try:
        payload = images_service_module.validate_generations_payload(payload)
    except ClientPayloadError as exc:
        return logged_error_json_response(request, 400, openai_client_payload_error(exc))

    public_model = payload.model
    assert public_model is not None
    host_model = settings.images_host_model

    try:
        validate_model_access(api_key, effective_model)
    except Exception:
        raise

    rate_limit_headers = await context.service.rate_limit_headers()
    reservation = await enforce_request_limits(
        api_key,
        request_model=effective_model,
        request_service_tier=None,
    )

    try:
        responses_payload = images_service_module.images_generation_to_responses_request(payload, host_model=host_model)
    except ValidationError as exc:
        await release_reservation(reservation)
        return logged_error_json_response(
            request,
            400,
            openai_validation_error(exc),
            headers=rate_limit_headers,
        )

    upstream = context.service.stream_responses(
        responses_payload,
        request.headers,
        codex_session_affinity=False,
        propagate_http_errors=True,
        openai_cache_affinity=True,
        api_key=api_key,
        api_key_reservation=None,
    )

    captured: dict[str, object] = {}

    primed_upstream, prime_error = await prime_upstream_stream(
        request,
        upstream,
        rate_limit_headers,
        on_error=lambda: release_reservation(reservation),
    )
    if prime_error is not None:
        return prime_error
    assert primed_upstream is not None

    if payload.stream:
        translated = images_service_module.translate_responses_stream_to_images_stream(
            primed_upstream, captured=captured
        )

        async def _stream_with_log_rewrite() -> AsyncIterator[bytes]:
            try:
                async for chunk in translated:
                    yield chunk.encode("utf-8") if isinstance(chunk, str) else chunk
            finally:
                response_id = captured.get("response_id")
                if response_id and isinstance(response_id, str):
                    await context.service.rewrite_request_log_model(response_id, public_model)

                _input = captured.get("image_input_tokens")
                _output = captured.get("image_output_tokens")
                _cached = captured.get("image_cached_input_tokens")
                await finalize_image_reservation(
                    reservation,
                    model=public_model,
                    input_tokens=_input if isinstance(_input, int) else None,
                    output_tokens=_output if isinstance(_output, int) else None,
                    cached_input_tokens=_cached if isinstance(_cached, int) else None,
                )

        return StreamingResponse(
            _stream_with_log_rewrite(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", **rate_limit_headers},
        )

    try:
        response_payload, error_envelope = await images_service_module.collect_responses_stream_for_images(
            primed_upstream,
            captured=captured,
        )
    except ProxyResponseError as exc:
        await release_reservation(reservation)
        return logged_error_json_response(
            request,
            exc.status_code,
            exc.payload,
            headers=rate_limit_headers,
        )

    response_id = captured.get("response_id")
    if response_id and isinstance(response_id, str):
        await context.service.rewrite_request_log_model(response_id, public_model)
    _input = captured.get("image_input_tokens")
    _output = captured.get("image_output_tokens")
    _cached = captured.get("image_cached_input_tokens")
    await finalize_image_reservation(
        reservation,
        model=public_model,
        input_tokens=_input if isinstance(_input, int) else None,
        output_tokens=_output if isinstance(_output, int) else None,
        cached_input_tokens=_cached if isinstance(_cached, int) else None,
    )

    if error_envelope is not None:
        return logged_error_json_response(
            request,
            status_for_image_error_envelope(error_envelope),
            error_envelope,
            headers=rate_limit_headers,
        )
    assert response_payload is not None
    images_result = images_service_module.images_response_from_responses(response_payload)
    if not isinstance(images_result, V1ImageResponse):
        return logged_error_json_response(
            request,
            status_for_image_error_envelope(images_result),
            images_result,
            headers=rate_limit_headers,
        )
    return JSONResponse(
        content=images_result.model_dump(mode="json", exclude_none=True),
        headers=rate_limit_headers,
    )


async def proxy_images_edit_request(
    *,
    request: Request,
    payload: V1ImagesEditsForm,
    images: list[tuple[bytes, str | None]],
    mask: tuple[bytes, str | None] | None,
    context: ProxyContext,
    api_key: ApiKeyData | None,
) -> Response:

    settings = proxy_service_module.get_settings()
    requested_model = payload.model
    effective_model = effective_model_for_api_key(
        api_key,
        requested_model or settings.images_default_model,
    )
    if not images_service_module.is_supported_image_model(effective_model):
        return logged_error_json_response(
            request,
            400,
            images_service_module.make_invalid_request_error(
                f"Effective model '{effective_model}' is not a 'gpt-image-*' model. "
                f"This API key is pinned to '{effective_model}' which cannot be used on "
                f"/v1/images/* routes; use a key that allows gpt-image models.",
                param="model",
            ),
        )
    if effective_model != requested_model:
        payload = payload.model_copy(update={"model": effective_model})

    try:
        payload = images_service_module.validate_edits_payload(payload)
    except ClientPayloadError as exc:
        return logged_error_json_response(request, 400, openai_client_payload_error(exc))

    public_model = payload.model
    assert public_model is not None
    host_model = settings.images_host_model

    validate_model_access(api_key, effective_model)

    rate_limit_headers = await context.service.rate_limit_headers()
    reservation = await enforce_request_limits(
        api_key,
        request_model=effective_model,
        request_service_tier=None,
    )

    try:
        responses_payload = images_service_module.images_edit_to_responses_request(
            payload,
            host_model=host_model,
            images=images,
            mask=mask,
        )
    except (ValidationError, ValueError) as exc:
        await release_reservation(reservation)
        if isinstance(exc, ValidationError):
            return logged_error_json_response(
                request,
                400,
                openai_validation_error(exc),
                headers=rate_limit_headers,
            )
        return logged_error_json_response(
            request,
            400,
            images_service_module.make_invalid_request_error(str(exc)),
            headers=rate_limit_headers,
        )

    upstream = context.service.stream_responses(
        responses_payload,
        request.headers,
        codex_session_affinity=False,
        propagate_http_errors=True,
        openai_cache_affinity=True,
        api_key=api_key,
        api_key_reservation=None,
    )

    captured: dict[str, object] = {}

    primed_upstream, prime_error = await prime_upstream_stream(
        request,
        upstream,
        rate_limit_headers,
        on_error=lambda: release_reservation(reservation),
    )
    if prime_error is not None:
        return prime_error
    assert primed_upstream is not None

    if payload.stream:
        translated = images_service_module.translate_responses_stream_to_images_stream(
            primed_upstream, captured=captured, is_edit=True
        )

        async def _stream_with_log_rewrite() -> AsyncIterator[bytes]:
            try:
                async for chunk in translated:
                    yield chunk.encode("utf-8") if isinstance(chunk, str) else chunk
            finally:
                response_id = captured.get("response_id")
                if response_id and isinstance(response_id, str):
                    await context.service.rewrite_request_log_model(response_id, public_model)

                _input = captured.get("image_input_tokens")
                _output = captured.get("image_output_tokens")
                _cached = captured.get("image_cached_input_tokens")
                await finalize_image_reservation(
                    reservation,
                    model=public_model,
                    input_tokens=_input if isinstance(_input, int) else None,
                    output_tokens=_output if isinstance(_output, int) else None,
                    cached_input_tokens=_cached if isinstance(_cached, int) else None,
                )

        return StreamingResponse(
            _stream_with_log_rewrite(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", **rate_limit_headers},
        )

    try:
        response_payload, error_envelope = await images_service_module.collect_responses_stream_for_images(
            primed_upstream,
            captured=captured,
        )
    except ProxyResponseError as exc:
        await release_reservation(reservation)
        return logged_error_json_response(
            request,
            exc.status_code,
            exc.payload,
            headers=rate_limit_headers,
        )

    response_id = captured.get("response_id")
    if response_id and isinstance(response_id, str):
        await context.service.rewrite_request_log_model(response_id, public_model)
    _input = captured.get("image_input_tokens")
    _output = captured.get("image_output_tokens")
    _cached = captured.get("image_cached_input_tokens")
    await finalize_image_reservation(
        reservation,
        model=public_model,
        input_tokens=_input if isinstance(_input, int) else None,
        output_tokens=_output if isinstance(_output, int) else None,
        cached_input_tokens=_cached if isinstance(_cached, int) else None,
    )

    if error_envelope is not None:
        return logged_error_json_response(
            request,
            status_for_image_error_envelope(error_envelope),
            error_envelope,
            headers=rate_limit_headers,
        )
    assert response_payload is not None
    images_result = images_service_module.images_response_from_responses(response_payload)
    if not isinstance(images_result, V1ImageResponse):
        return logged_error_json_response(
            request,
            status_for_image_error_envelope(images_result),
            images_result,
            headers=rate_limit_headers,
        )
    return JSONResponse(
        content=images_result.model_dump(mode="json", exclude_none=True),
        headers=rate_limit_headers,
    )
