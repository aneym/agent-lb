from __future__ import annotations

import base64
import binascii
import logging
import re
import time
from collections.abc import Mapping
from typing import Final, cast

from app.core.config.settings import get_settings
from app.core.errors import OpenAIErrorEnvelope, openai_error
from app.core.openai.exceptions import ClientPayloadError
from app.core.openai.images import (
    V1ImageData,
    V1ImageResponse,
    V1ImagesEditsForm,
    V1ImagesGenerationsRequest,
    is_supported_image_model,
    validate_image_request_parameters,
)
from app.core.openai.requests import ResponsesRequest
from app.core.types import JsonValue
from app.core.utils.json_guards import is_json_mapping
from app.modules.proxy.image_stream import (
    collect_responses_stream_for_images,
    translate_responses_stream_to_images_stream,
)
from app.modules.proxy.image_usage import _extract_image_usage

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
