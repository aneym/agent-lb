from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.core.clients.proxy import ProxyResponseError
from app.core.errors import response_failed_event
from app.core.openai.models import OpenAIErrorEnvelope as OpenAIErrorEnvelopeModel
from app.core.openai.parsing import parse_response_payload
from app.core.utils.sse import format_sse_event, parse_sse_data_json
from app.modules.api_keys.service_data import ApiKeyUsageReservationData
from app.modules.proxy.constants import (
    CHAT_COMPLETIONS_STARTUP_ERROR_PROBE_SECONDS,
    CHAT_COMPLETIONS_STARTUP_EVENT_TYPES,
    STREAM_STARTUP_ERROR_PROBE_SECONDS,
    logger,
)
from app.modules.proxy.reservations import release_reservation
from app.modules.proxy.response_errors import (
    default_error_envelope,
    error_envelope_from_response,
    logged_error_json_response,
    mask_previous_response_not_found_error,
    parse_error_envelope,
    parse_event_error_envelope,
)
from app.modules.proxy.stream_iteration import prepend_first, prepend_first_task, prepend_items, read_first_stream_item


async def probe_stream_startup_error(
    stream: AsyncIterator[str],
    *,
    convert_event_errors: bool = False,
    timeout_seconds: float | None = None,
) -> tuple[AsyncIterator[str], ProxyResponseError | OpenAIErrorEnvelopeModel | None]:
    if timeout_seconds is None:
        timeout_seconds = STREAM_STARTUP_ERROR_PROBE_SECONDS
    first_task = asyncio.create_task(read_first_stream_item(stream))
    try:
        first = await asyncio.wait_for(
            asyncio.shield(first_task),
            timeout=timeout_seconds,
        )
    except TimeoutError:
        return prepend_first_task(first_task, stream), None
    except StopAsyncIteration:
        return prepend_first(None, stream), None
    except ProxyResponseError as exc:
        return prepend_first(None, stream), exc
    if convert_event_errors:
        first_error = stream_event_error_envelope(first)
        if first_error is not None:
            aclose = getattr(stream, "aclose", None)
            if callable(aclose):
                await aclose()
            return prepend_first(None, stream), first_error
    return prepend_first(first, stream), None


async def probe_chat_stream_startup_error(
    stream: AsyncIterator[str],
    *,
    timeout_seconds: float = CHAT_COMPLETIONS_STARTUP_ERROR_PROBE_SECONDS,
    max_startup_events: int = 8,
) -> tuple[AsyncIterator[str], ProxyResponseError | OpenAIErrorEnvelopeModel | None]:
    buffered: list[str] = []
    for _ in range(max_startup_events):
        first_task = asyncio.create_task(read_first_stream_item(stream))
        try:
            first = await asyncio.wait_for(
                asyncio.shield(first_task),
                timeout=timeout_seconds,
            )
        except TimeoutError:
            return prepend_items(buffered, prepend_first_task(first_task, stream)), None
        except StopAsyncIteration:
            return prepend_items(buffered, prepend_first(None, stream)), None
        except ProxyResponseError as exc:
            return prepend_items(buffered, prepend_first(None, stream)), exc

        first_error = stream_event_error_envelope(first)
        if first_error is not None:
            aclose = getattr(stream, "aclose", None)
            if callable(aclose):
                await aclose()
            return prepend_first(None, stream), first_error

        payload = parse_sse_data_json(first)
        event_type = payload.get("type") if payload else None
        buffered.append(first)
        if event_type in CHAT_COMPLETIONS_STARTUP_EVENT_TYPES:
            continue
        return prepend_items(buffered, stream), None
    return prepend_items(buffered, stream), None


async def stream_proxy_errors_as_response_failed(stream: AsyncIterator[str]) -> AsyncIterator[str]:
    async for line in stream_response_error_events(stream, owns_reservation=False, reservation=None):
        yield line


async def stream_response_error_events(
    stream: AsyncIterator[str],
    *,
    owns_reservation: bool,
    reservation: ApiKeyUsageReservationData | None,
) -> AsyncIterator[str]:
    try:
        async for line in stream:
            yield line
    except ProxyResponseError as exc:
        if owns_reservation:
            try:
                await release_reservation(reservation)
            except Exception:
                logger.warning("Failed to release stream reservation after upstream proxy error", exc_info=True)
        envelope = parse_error_envelope(exc.payload)
        _, envelope = mask_previous_response_not_found_error(envelope, default_status=exc.status_code)
        error = envelope.error
        yield format_sse_event(
            response_failed_event(
                error.code if error and error.code else "upstream_error",
                error.message if error and error.message else "Upstream error",
                error.type if error and error.type else "server_error",
                error_param=error.param if error else None,
            )
        )


def stream_startup_error_response(
    request: Request,
    error: ProxyResponseError | OpenAIErrorEnvelopeModel,
    *,
    headers: Mapping[str, str],
) -> JSONResponse:
    if isinstance(error, ProxyResponseError):
        envelope = parse_error_envelope(error.payload)
        status_code, envelope = mask_previous_response_not_found_error(envelope, default_status=error.status_code)
        return logged_error_json_response(
            request,
            status_code,
            envelope.model_dump(mode="json", exclude_none=True),
            headers=headers,
        )
    status_code, envelope = mask_previous_response_not_found_error(error)
    return logged_error_json_response(
        request,
        status_code,
        envelope.model_dump(mode="json", exclude_none=True),
        headers=headers,
    )


def stream_event_error_envelope(event_block: str) -> OpenAIErrorEnvelopeModel | None:
    payload = parse_sse_data_json(event_block)
    if payload is None:
        return None
    event_type = payload.get("type")
    if event_type == "error":
        return parse_event_error_envelope(payload)
    if event_type != "response.failed":
        return None
    response = payload.get("response")
    if not isinstance(response, dict):
        return default_error_envelope()
    error_value = response.get("error")
    if isinstance(error_value, dict):
        try:
            return OpenAIErrorEnvelopeModel.model_validate({"error": error_value})
        except ValidationError:
            return default_error_envelope()
    parsed = parse_response_payload(response)
    if parsed is not None and parsed.error is not None:
        return error_envelope_from_response(parsed.error)
    return default_error_envelope()
