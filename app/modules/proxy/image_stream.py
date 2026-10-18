from __future__ import annotations

import time
from collections.abc import AsyncIterator, Mapping
from typing import Final, cast

from app.core.errors import OpenAIErrorEnvelope, openai_error
from app.core.types import JsonValue
from app.core.utils.json_guards import is_json_mapping
from app.core.utils.sse import format_sse_event, parse_sse_data_json
from app.modules.proxy.image_usage import _extract_image_usage, _stash_image_usage_tokens

_UPSTREAM_PARTIAL_IMAGE_EVENT: Final[str] = "response.image_generation_call.partial_image"


_UPSTREAM_OUTPUT_ITEM_DONE_EVENT: Final[str] = "response.output_item.done"


_UPSTREAM_RESPONSE_COMPLETED_EVENT: Final[str] = "response.completed"


_UPSTREAM_RESPONSE_FAILED_EVENT: Final[str] = "response.failed"


_UPSTREAM_RESPONSE_INCOMPLETE_EVENT: Final[str] = "response.incomplete"


_UPSTREAM_ERROR_EVENT: Final[str] = "error"


_GENERATION_PARTIAL_EVENT: Final[str] = "image_generation.partial_image"


_GENERATION_COMPLETED_EVENT: Final[str] = "image_generation.completed"


_EDIT_PARTIAL_EVENT: Final[str] = "image_edit.partial_image"


_EDIT_COMPLETED_EVENT: Final[str] = "image_edit.completed"


_DOWNSTREAM_ERROR_EVENT: Final[str] = "error"


def _build_partial_image_event(payload: Mapping[str, JsonValue], *, event_type: str) -> dict[str, JsonValue] | None:
    partial_b64 = payload.get("partial_image_b64")
    if not isinstance(partial_b64, str) or not partial_b64:
        return None
    event: dict[str, JsonValue] = {
        "type": event_type,
        "b64_json": partial_b64,
        "created_at": _coerce_created_at(payload.get("created_at")),
    }
    for key in ("partial_image_index", "size", "quality", "background", "output_format", "output_index"):
        value = payload.get(key)
        if value is not None:
            event[key] = value
    return event


def _build_completed_event(item: Mapping[str, JsonValue], *, event_type: str) -> dict[str, JsonValue] | None:
    if item.get("type") != "image_generation_call":
        return None
    result = item.get("result")
    if not isinstance(result, str) or not result:
        return None
    event: dict[str, JsonValue] = {
        "type": event_type,
        "b64_json": result,
        "created_at": _coerce_created_at(item.get("created_at")),
    }
    for key in ("revised_prompt", "size", "quality", "background", "output_format"):
        value = item.get(key)
        if value is not None:
            event[key] = value
    return event


def _coerce_created_at(value: JsonValue | None) -> int:

    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        return int(value)
    return int(time.time())


def _build_error_event(
    code: str,
    message: str,
    *,
    error_type: str = "server_error",
    param: str | None = None,
) -> dict[str, JsonValue]:
    envelope = openai_error(code, message, error_type=error_type)
    if param:
        envelope["error"]["param"] = param
    event: dict[str, JsonValue] = {"type": _DOWNSTREAM_ERROR_EVENT}
    for key, value in envelope.items():
        event[key] = cast(JsonValue, value)
    return event


def _failed_image_item_error_event(item: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    error_value = item.get("error")
    if is_json_mapping(error_value):
        code = error_value.get("code")
        message = error_value.get("message")
        error_type = error_value.get("type")
        return _build_error_event(
            code if isinstance(code, str) and code else "image_generation_failed",
            message if isinstance(message, str) and message else "Image generation failed",
            error_type=error_type if isinstance(error_type, str) and error_type else "server_error",
        )
    return _build_error_event(
        "image_generation_failed",
        "Upstream image_generation_call reported status=failed",
    )


def _response_failed_to_error_event(payload: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    response = payload.get("response")
    if is_json_mapping(response):
        error_value = response.get("error")
        if is_json_mapping(error_value):
            code = error_value.get("code")
            message = error_value.get("message")
            error_type = error_value.get("type")
            return _build_error_event(
                code if isinstance(code, str) and code else "upstream_error",
                message if isinstance(message, str) and message else "Upstream image generation failed",
                error_type=error_type if isinstance(error_type, str) and error_type else "server_error",
            )
    return _build_error_event("upstream_error", "Upstream image generation failed")


def _error_event_to_error_event(payload: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    error_value = payload.get("error")
    if is_json_mapping(error_value):
        code = error_value.get("code")
        message = error_value.get("message")
        error_type = error_value.get("type")
        return _build_error_event(
            code if isinstance(code, str) and code else "upstream_error",
            message if isinstance(message, str) and message else "Upstream image generation failed",
            error_type=error_type if isinstance(error_type, str) and error_type else "server_error",
        )
    return _build_error_event("upstream_error", "Upstream image generation failed")


async def translate_responses_stream_to_images_stream(
    upstream: AsyncIterator[str],
    *,
    captured: dict[str, object] | None = None,
    is_edit: bool = False,
) -> AsyncIterator[str]:

    partial_event_type = _EDIT_PARTIAL_EVENT if is_edit else _GENERATION_PARTIAL_EVENT
    completed_event_type = _EDIT_COMPLETED_EVENT if is_edit else _GENERATION_COMPLETED_EVENT
    terminal_emitted = False
    completion_pending = True

    pending_completed_events: list[dict[str, JsonValue]] = []

    async for line in upstream:
        if not line:
            continue
        stripped = line.strip()
        if stripped == "data: [DONE]":
            continue
        payload = parse_sse_data_json(line)
        if payload is None:
            continue
        event_type = payload.get("type")
        if not isinstance(event_type, str):
            continue

        if captured is not None and "response_id" not in captured:
            response_value = payload.get("response")
            if is_json_mapping(response_value):
                response_id = response_value.get("id")
                if isinstance(response_id, str) and response_id:
                    captured["response_id"] = response_id

        if event_type == _UPSTREAM_PARTIAL_IMAGE_EVENT:
            event = _build_partial_image_event(payload, event_type=partial_event_type)
            if event is not None:
                yield format_sse_event(event)
            continue

        if event_type == _UPSTREAM_OUTPUT_ITEM_DONE_EVENT:
            item = payload.get("item")
            if not is_json_mapping(item):
                continue
            if item.get("type") != "image_generation_call":
                continue
            status = item.get("status")
            if isinstance(status, str) and status == "failed":
                yield format_sse_event(_failed_image_item_error_event(item))
                terminal_emitted = True
                completion_pending = False
                break
            event = _build_completed_event(item, event_type=completed_event_type)
            if event is not None:
                pending_completed_events.append(event)
            continue

        if event_type == _UPSTREAM_RESPONSE_COMPLETED_EVENT:
            response_obj = payload.get("response")
            usage = _extract_image_usage(response_obj) if is_json_mapping(response_obj) else None

            if captured is not None and usage is not None:
                _stash_image_usage_tokens(captured, usage)
            if pending_completed_events:
                last_index = len(pending_completed_events) - 1
                for idx, event in enumerate(pending_completed_events):
                    if idx == last_index and usage is not None:
                        event["usage"] = usage.model_dump(mode="json", exclude_none=True)
                    yield format_sse_event(event)
                pending_completed_events.clear()
                terminal_emitted = True
            elif not terminal_emitted:
                yield format_sse_event(
                    _build_error_event(
                        "image_generation_failed",
                        "Upstream stream completed without an image_generation_call result",
                    )
                )
                terminal_emitted = True
            completion_pending = False
            break

        if event_type == _UPSTREAM_RESPONSE_INCOMPLETE_EVENT:
            if not terminal_emitted:
                yield format_sse_event(
                    _build_error_event(
                        "image_generation_failed",
                        "Upstream stream ended before the image was generated",
                    )
                )
                terminal_emitted = True
            completion_pending = False
            break

        if event_type == _UPSTREAM_RESPONSE_FAILED_EVENT:
            yield format_sse_event(_response_failed_to_error_event(payload))
            terminal_emitted = True
            completion_pending = False
            break

        if event_type == _UPSTREAM_ERROR_EVENT:
            yield format_sse_event(_error_event_to_error_event(payload))
            terminal_emitted = True
            completion_pending = False
            break

        continue

    if pending_completed_events and not terminal_emitted:
        for event in pending_completed_events:
            yield format_sse_event(event)
        pending_completed_events.clear()
        terminal_emitted = True

    if completion_pending and not terminal_emitted:
        yield format_sse_event(
            _build_error_event(
                "image_generation_failed",
                "Upstream stream truncated before a terminal image event",
            )
        )

    yield "data: [DONE]\n\n"


async def collect_responses_stream_for_images(
    upstream: AsyncIterator[str],
    *,
    captured: dict[str, object] | None = None,
) -> tuple[dict[str, JsonValue] | None, OpenAIErrorEnvelope | None]:

    output_items: dict[int, dict[str, JsonValue]] = {}
    fallback_items: list[dict[str, JsonValue]] = []
    final_response: dict[str, JsonValue] | None = None
    terminal_error: OpenAIErrorEnvelope | None = None

    async for line in upstream:
        if not line:
            continue
        if line.strip() == "data: [DONE]":
            continue
        payload = parse_sse_data_json(line)
        if payload is None:
            continue
        event_type = payload.get("type")
        if not isinstance(event_type, str):
            continue

        if captured is not None and "response_id" not in captured:
            response_value = payload.get("response")
            if is_json_mapping(response_value):
                response_id = response_value.get("id")
                if isinstance(response_id, str) and response_id:
                    captured["response_id"] = response_id

        if event_type == _UPSTREAM_OUTPUT_ITEM_DONE_EVENT:
            output_index = payload.get("output_index")
            item = payload.get("item")
            if not is_json_mapping(item):
                continue
            if isinstance(output_index, int):
                output_items[output_index] = dict(item)
            else:
                fallback_items.append(dict(item))
            continue

        if event_type == _UPSTREAM_RESPONSE_COMPLETED_EVENT and final_response is None:
            response_value = payload.get("response")
            base: dict[str, JsonValue]
            if is_json_mapping(response_value):
                base = dict(response_value)
            else:
                base = {}
            existing_output = base.get("output")
            if not (isinstance(existing_output, list) and existing_output):
                merged_output: list[JsonValue] = [item for _, item in sorted(output_items.items())]
                merged_output.extend(fallback_items)
                base["output"] = merged_output
            final_response = base

            if captured is not None:
                tool_usage_payload = base
                usage = _extract_image_usage(tool_usage_payload)
                if usage is not None:
                    _stash_image_usage_tokens(captured, usage)

            continue

        if event_type == _UPSTREAM_RESPONSE_INCOMPLETE_EVENT and terminal_error is None:
            terminal_error = openai_error(
                "image_generation_failed",
                "Upstream response was incomplete before the image was generated",
                error_type="server_error",
            )
            break

        if event_type == _UPSTREAM_RESPONSE_FAILED_EVENT:
            if final_response is not None:
                continue
            response_value = payload.get("response")
            error_value: JsonValue | None = None
            if is_json_mapping(response_value):
                error_value = response_value.get("error")
            if is_json_mapping(error_value):
                code = error_value.get("code")
                message = error_value.get("message")
                error_type = error_value.get("type")
                envelope = openai_error(
                    code if isinstance(code, str) and code else "upstream_error",
                    message if isinstance(message, str) and message else "Upstream image generation failed",
                    error_type=error_type if isinstance(error_type, str) and error_type else "server_error",
                )
            else:
                envelope = openai_error(
                    "upstream_error",
                    "Upstream image generation failed",
                    error_type="server_error",
                )
            terminal_error = envelope
            break

        if event_type == _UPSTREAM_ERROR_EVENT:
            if final_response is not None:
                continue
            error_value = payload.get("error")
            if is_json_mapping(error_value):
                code = error_value.get("code")
                message = error_value.get("message")
                error_type = error_value.get("type")
                envelope = openai_error(
                    code if isinstance(code, str) and code else "upstream_error",
                    message if isinstance(message, str) and message else "Upstream image generation failed",
                    error_type=error_type if isinstance(error_type, str) and error_type else "server_error",
                )
            else:
                envelope = openai_error(
                    "upstream_error",
                    "Upstream image generation failed",
                    error_type="server_error",
                )
            terminal_error = envelope
            break

    if terminal_error is not None:
        return None, terminal_error
    if final_response is None:
        return None, openai_error(
            "image_generation_failed",
            "Upstream stream truncated before a terminal event",
            error_type="server_error",
        )
    return final_response, None
