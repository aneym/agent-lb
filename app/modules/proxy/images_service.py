from __future__ import annotations

import base64
import binascii
import logging
import re
import time
from collections.abc import AsyncIterator, Mapping
from typing import Final, cast

from app.core.config.settings import get_settings
from app.core.errors import OpenAIErrorEnvelope, openai_error
from app.core.openai.exceptions import ClientPayloadError
from app.core.openai.images import (
    V1ImageData,
    V1ImageResponse,
    V1ImagesEditsForm,
    V1ImagesGenerationsRequest,
    V1ImageUsage,
    is_supported_image_model,
    validate_image_request_parameters,
)
from app.core.openai.requests import ResponsesRequest
from app.core.types import JsonValue
from app.core.utils.json_guards import is_json_mapping
from app.core.utils.sse import format_sse_event, parse_sse_data_json

logger = logging.getLogger(__name__)


_IMAGE_GENERATION_INSTRUCTIONS: Final[str] = (
    "You are an image generator. When asked, you MUST call the image_generation "
    "tool exactly once and return only that tool call. Do not produce any "
    "additional text output. Mirror the user's request verbatim into the tool's "
    "prompt argument."
)


_IMAGE_EDIT_MASK_HINT: Final[str] = (
    "\n\n(The final attached image is a transparent mask: only modify the regions where the mask is non-transparent.)"
)


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

_DATA_URL_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^data:(?P<mime>[^;]+);base64,(?P<b64>.+)$",
    re.DOTALL,
)


def _build_image_generation_tool(
    *,
    model: str,
    n: int,
    size: str,
    quality: str,
    background: str,
    output_format: str,
    output_compression: int,
    moderation: str,
    partial_images: int | None,
    input_fidelity: str | None,
    streaming: bool,
    is_edit: bool = False,
) -> dict[str, JsonValue]:

    assert n == 1, "image_generation tool does not accept n; fan-out is not implemented"
    del n
    tool: dict[str, JsonValue] = {
        "type": "image_generation",
        "model": model,
        "size": size,
        "quality": quality,
        "background": background,
        "output_format": output_format,
        "output_compression": output_compression,
        "moderation": moderation,
    }
    if is_edit:
        tool["action"] = "edit"
    if input_fidelity is not None:
        tool["input_fidelity"] = input_fidelity
    if streaming and partial_images is not None and partial_images > 0:
        tool["partial_images"] = partial_images
    return tool


def _build_user_message_input(
    prompt: str, *, attached_images: list[dict[str, JsonValue]] | None = None
) -> list[JsonValue]:
    content: list[JsonValue] = [{"type": "input_text", "text": prompt}]
    if attached_images:
        content.extend(attached_images)
    return [
        {
            "type": "message",
            "role": "user",
            "content": content,
        }
    ]


def _build_input_image_part(image_bytes: bytes, *, mime_type: str | None) -> dict[str, JsonValue]:

    resolved_mime = (mime_type or "image/png").strip() or "image/png"
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return {
        "type": "input_image",
        "image_url": f"data:{resolved_mime};base64,{encoded}",
    }


def images_generation_to_responses_request(
    payload: V1ImagesGenerationsRequest,
    *,
    host_model: str,
) -> ResponsesRequest:

    streaming = bool(payload.stream)

    assert payload.model is not None, "payload.model must be resolved before translation"
    tool = _build_image_generation_tool(
        model=payload.model,
        n=payload.n,
        size=payload.size,
        quality=payload.quality,
        background=payload.background,
        output_format=payload.output_format,
        output_compression=payload.output_compression,
        moderation=payload.moderation,
        partial_images=payload.partial_images,
        input_fidelity=None,
        streaming=streaming,
    )
    return ResponsesRequest.model_validate(
        {
            "model": host_model,
            "instructions": _IMAGE_GENERATION_INSTRUCTIONS,
            "input": _build_user_message_input(payload.prompt),
            "tools": [tool],
            "tool_choice": {"type": "image_generation"},
            "stream": True,
            "store": False,
        }
    )


def images_edit_to_responses_request(
    payload: V1ImagesEditsForm,
    *,
    host_model: str,
    images: list[tuple[bytes, str | None]],
    mask: tuple[bytes, str | None] | None,
) -> ResponsesRequest:

    if not images:
        raise ValueError("/v1/images/edits requires at least one image part")

    streaming = bool(payload.stream)
    attached: list[dict[str, JsonValue]] = []
    for image_bytes, mime_type in images:
        attached.append(_build_input_image_part(image_bytes, mime_type=mime_type))
    if mask is not None:
        mask_bytes, mask_mime = mask
        attached.append(_build_input_image_part(mask_bytes, mime_type=mask_mime))

    prompt_text = payload.prompt
    if mask is not None:
        prompt_text = f"{prompt_text}{_IMAGE_EDIT_MASK_HINT}"

    assert payload.model is not None, "payload.model must be resolved before translation"
    tool = _build_image_generation_tool(
        model=payload.model,
        n=payload.n,
        size=payload.size,
        quality=payload.quality,
        background=payload.background,
        output_format=payload.output_format,
        output_compression=payload.output_compression,
        moderation=payload.moderation,
        partial_images=payload.partial_images,
        input_fidelity=payload.input_fidelity,
        streaming=streaming,
        is_edit=True,
    )
    return ResponsesRequest.model_validate(
        {
            "model": host_model,
            "instructions": _IMAGE_GENERATION_INSTRUCTIONS,
            "input": _build_user_message_input(prompt_text, attached_images=attached),
            "tools": [tool],
            "tool_choice": {"type": "image_generation"},
            "stream": True,
            "store": False,
        }
    )


def resolve_public_image_model(requested: str | None) -> str:

    settings = get_settings()
    resolved = requested or settings.images_default_model
    if not is_supported_image_model(resolved):
        raise ClientPayloadError(
            f"Unsupported image model '{resolved}'. Use a 'gpt-image-*' model.",
            param="model",
            code="invalid_request_error",
            error_type="invalid_request_error",
        )
    return resolved


def validate_generations_payload(payload: V1ImagesGenerationsRequest) -> V1ImagesGenerationsRequest:

    settings = get_settings()
    resolved_model = resolve_public_image_model(payload.model)

    validate_image_request_parameters(
        model=resolved_model,
        quality=payload.quality,
        size=payload.size,
        background=payload.background,
        output_format=payload.output_format,
        moderation=payload.moderation,
        input_fidelity=payload.input_fidelity,
        is_edit=False,
        n=payload.n,
        partial_images=payload.partial_images,
        output_compression=payload.output_compression,
        images_max_partial_images=settings.images_max_partial_images,
    )
    if payload.model != resolved_model:
        return payload.model_copy(update={"model": resolved_model})
    return payload


def validate_edits_payload(payload: V1ImagesEditsForm) -> V1ImagesEditsForm:

    settings = get_settings()
    resolved_model = resolve_public_image_model(payload.model)
    validate_image_request_parameters(
        model=resolved_model,
        quality=payload.quality,
        size=payload.size,
        background=payload.background,
        output_format=payload.output_format,
        moderation=payload.moderation,
        input_fidelity=payload.input_fidelity,
        is_edit=True,
        n=payload.n,
        partial_images=payload.partial_images,
        output_compression=payload.output_compression,
        images_max_partial_images=settings.images_max_partial_images,
    )
    if payload.model != resolved_model:
        return payload.model_copy(update={"model": resolved_model})
    return payload


def _select_image_items(output: list[JsonValue]) -> list[Mapping[str, JsonValue]]:
    items: list[Mapping[str, JsonValue]] = []
    for entry in output:
        if not is_json_mapping(entry):
            continue
        if entry.get("type") == "image_generation_call":
            items.append(entry)
    return items


def _coerce_int(value: JsonValue | None) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None


_USAGE_TOKEN_FIELDS: Final[frozenset[str]] = frozenset({"input_tokens", "output_tokens", "total_tokens"})


def _extract_image_usage(response: Mapping[str, JsonValue]) -> V1ImageUsage | None:
    tool_usage = response.get("tool_usage")
    if not is_json_mapping(tool_usage):
        return None
    image_usage = tool_usage.get("image_gen")
    if not is_json_mapping(image_usage):
        return None
    input_tokens = _coerce_int(image_usage.get("input_tokens"))
    output_tokens = _coerce_int(image_usage.get("output_tokens"))
    total_tokens = _coerce_int(image_usage.get("total_tokens"))
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens
    input_details_raw = image_usage.get("input_tokens_details")
    output_details_raw = image_usage.get("output_tokens_details")
    input_details = dict(input_details_raw) if is_json_mapping(input_details_raw) else None
    output_details = dict(output_details_raw) if is_json_mapping(output_details_raw) else None

    extra_usage: dict[str, JsonValue] = {}
    for key, value in image_usage.items():
        if key in _USAGE_TOKEN_FIELDS:
            continue
        if key in ("input_tokens_details", "output_tokens_details"):
            continue
        extra_usage[key] = value
    if (
        input_tokens is None
        and output_tokens is None
        and total_tokens is None
        and input_details is None
        and output_details is None
        and not extra_usage
    ):
        return None
    return V1ImageUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        input_tokens_details=input_details,
        output_tokens_details=output_details,
        **extra_usage,
    )


def images_response_from_responses(response: Mapping[str, JsonValue]) -> V1ImageResponse | OpenAIErrorEnvelope:

    output_value = response.get("output")
    if not isinstance(output_value, list):
        return openai_error(
            "image_generation_failed",
            "Upstream response did not include an output array",
            error_type="server_error",
        )
    items = _select_image_items(cast(list[JsonValue], output_value))
    if not items:
        return openai_error(
            "image_generation_failed",
            "Upstream response did not include any image_generation_call items",
            error_type="server_error",
        )

    for item in items:
        status = item.get("status")
        if isinstance(status, str) and status == "failed":
            error = item.get("error")
            if is_json_mapping(error):
                message = error.get("message")
                code = error.get("code")
                error_type = error.get("type")
                return openai_error(
                    code if isinstance(code, str) and code else "image_generation_failed",
                    message if isinstance(message, str) and message else "Image generation failed",
                    error_type=error_type if isinstance(error_type, str) and error_type else "server_error",
                )
            return openai_error(
                "image_generation_failed",
                "Upstream image_generation_call reported status=failed",
                error_type="server_error",
            )

    data_entries: list[V1ImageData] = []
    for item in items:
        result = item.get("result")
        if not isinstance(result, str) or not result:
            continue
        revised_prompt = item.get("revised_prompt")
        data_entries.append(
            V1ImageData(
                b64_json=result,
                revised_prompt=revised_prompt if isinstance(revised_prompt, str) and revised_prompt else None,
            )
        )

    if not data_entries:
        return openai_error(
            "image_generation_failed",
            "Upstream image_generation_call items contained no image data",
            error_type="server_error",
        )

    usage = _extract_image_usage(response)
    return V1ImageResponse(
        created=int(time.time()),
        data=data_entries,
        usage=usage,
    )


def _stash_image_usage_tokens(captured: dict[str, object], usage: V1ImageUsage) -> None:

    if usage.input_tokens is not None:
        captured["image_input_tokens"] = int(usage.input_tokens)
    if usage.output_tokens is not None:
        captured["image_output_tokens"] = int(usage.output_tokens)
    cached = _extract_cached_input_tokens(usage)
    if cached is not None:
        captured["image_cached_input_tokens"] = cached


def _extract_cached_input_tokens(usage: V1ImageUsage) -> int | None:
    details = usage.input_tokens_details
    if not isinstance(details, Mapping):
        return None
    raw = details.get("cached_tokens")
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        return int(raw)
    return None


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


def decode_data_url(data_url: str) -> tuple[bytes, str | None]:

    match = _DATA_URL_PATTERN.match(data_url.strip())
    if match is None:
        raise ValueError("Expected a data:<mime>;base64,<payload> URL")
    mime_type = match.group("mime") or None
    try:
        return base64.b64decode(match.group("b64"), validate=True), mime_type
    except (binascii.Error, ValueError) as exc:
        raise ValueError("Invalid base64 image payload") from exc


def make_invalid_request_error(
    message: str,
    *,
    param: str | None = None,
    code: str = "invalid_request_error",
) -> OpenAIErrorEnvelope:
    envelope = openai_error(code, message, error_type="invalid_request_error")
    if param:
        envelope["error"]["param"] = param
    return envelope


def make_not_found_error(message: str) -> OpenAIErrorEnvelope:
    return openai_error("not_found_error", message, error_type="invalid_request_error")


__all__ = [
    "collect_responses_stream_for_images",
    "decode_data_url",
    "images_edit_to_responses_request",
    "images_generation_to_responses_request",
    "images_response_from_responses",
    "is_supported_image_model",
    "make_invalid_request_error",
    "make_not_found_error",
    "resolve_public_image_model",
    "translate_responses_stream_to_images_stream",
    "validate_edits_payload",
    "validate_generations_payload",
]
