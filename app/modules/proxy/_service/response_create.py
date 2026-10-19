from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from copy import deepcopy
from hashlib import sha256
from typing import TypeVar, cast

from app.core.clients.proxy import (
    ImageFetchSession,
    ProxyResponseError,
    _inline_content_images,
)
from app.core.config.settings import get_settings
from app.core.errors import OpenAIErrorEnvelope, openai_error
from app.core.openai.requests import ResponsesRequest
from app.core.types import JsonValue
from app.core.utils.json_guards import is_json_mapping
from app.modules.proxy._service.response_create_diagnostics import _service_global_or, _write_response_create_dump
from app.modules.proxy._service.support import _WebSocketRequestState

logger = logging.getLogger("app.modules.proxy.service")
T = TypeVar("T")

_UPSTREAM_RESPONSE_CREATE_MAX_BYTES = get_settings().upstream_response_create_max_bytes
_UPSTREAM_RESPONSE_CREATE_WARN_BYTES = int(_UPSTREAM_RESPONSE_CREATE_MAX_BYTES * 0.8)
_RESPONSE_CREATE_HISTORY_OMISSION_NOTICE = (
    "[agent-lb omitted {count} historical input items to fit upstream websocket budget]"
)
_RESPONSE_CREATE_TOOL_OUTPUT_OMISSION_NOTICE = (
    "[agent-lb omitted historical tool output ({bytes} bytes) to fit upstream websocket budget]"
)
_RESPONSE_CREATE_IMAGE_OMISSION_NOTICE = "[agent-lb omitted historical inline image to fit upstream websocket budget]"


def _upstream_response_create_max_bytes() -> int:
    return int(_service_global_or("_UPSTREAM_RESPONSE_CREATE_MAX_BYTES", _UPSTREAM_RESPONSE_CREATE_MAX_BYTES))


def _upstream_response_create_warn_bytes() -> int:
    fallback = int(_upstream_response_create_max_bytes() * 0.8)
    return int(_service_global_or("_UPSTREAM_RESPONSE_CREATE_WARN_BYTES", fallback))


def _fingerprint_input_items(items: Sequence[JsonValue]) -> str:

    canonical = json.dumps(list(items), ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return sha256(canonical.encode("utf-8")).hexdigest()


def _input_part_is_image(part: JsonValue) -> bool:
    return is_json_mapping(part) and part.get("type") == "input_image"


def _json_value_contains_input_image_part(value: JsonValue) -> bool:
    if _input_part_is_image(value):
        return True
    if isinstance(value, list):
        return any(_json_value_contains_input_image_part(item) for item in value)
    if is_json_mapping(value):
        return any(_json_value_contains_input_image_part(child) for child in value.values())
    return False


def _responses_request_contains_input_image(payload: ResponsesRequest) -> bool:

    input_value = payload.input
    if not isinstance(input_value, list):
        return False
    return any(_json_value_contains_input_image_part(item) for item in input_value)


def _responses_request_uses_image_generation(payload: ResponsesRequest) -> bool:
    tools = payload.tools
    if not isinstance(tools, list):
        return False
    return any(is_json_mapping(tool) and tool.get("type") == "image_generation" for tool in tools)


def _response_create_text(
    payload: ResponsesRequest,
    *,
    include_type_field: bool,
    client_metadata: Mapping[str, JsonValue] | None,
) -> str:
    upstream_payload = dict(payload.to_payload())
    upstream_payload.pop("stream", None)
    upstream_payload.pop("background", None)
    if include_type_field:
        upstream_payload["type"] = "response.create"
    if client_metadata:
        upstream_payload["client_metadata"] = client_metadata
    return json.dumps(upstream_payload, ensure_ascii=True, separators=(",", ":"))


def _response_create_text_with_size_guard(
    payload: ResponsesRequest,
    *,
    include_type_field: bool,
    client_metadata: Mapping[str, JsonValue] | None,
    request_state: _WebSocketRequestState,
    transport: str,
) -> str | None:
    upstream_payload = dict(payload.to_payload())
    upstream_payload.pop("stream", None)
    upstream_payload.pop("background", None)
    if include_type_field:
        upstream_payload["type"] = "response.create"
    if client_metadata:
        upstream_payload["client_metadata"] = client_metadata
    text_data = json.dumps(upstream_payload, ensure_ascii=True, separators=(",", ":"))
    payload_size = len(text_data.encode("utf-8"))
    max_bytes = _upstream_response_create_max_bytes()
    if payload_size > max_bytes:
        original_payload_size = payload_size
        slim_payload_for_upstream = _service_global_or(
            "_slim_response_create_payload_for_upstream",
            _slim_response_create_payload_for_upstream,
        )
        slimmed_payload, slim_summary = slim_payload_for_upstream(
            upstream_payload,
            max_bytes=max_bytes,
        )
        if slim_summary is not None:
            upstream_payload = slimmed_payload
            text_data = json.dumps(upstream_payload, ensure_ascii=True, separators=(",", ":"))
            payload_size = len(text_data.encode("utf-8"))
            logger.warning(
                (
                    "Slimmed response.create request_id=%s request_log_id=%s transport=%s "
                    "original_bytes=%s slimmed_bytes=%s "
                    "historical_tool_outputs_slimmed=%s historical_images_slimmed=%s"
                ),
                request_state.request_id,
                request_state.request_log_id,
                transport,
                original_payload_size,
                payload_size,
                slim_summary["historical_tool_outputs_slimmed"],
                slim_summary["historical_images_slimmed"],
            )
        if payload_size > max_bytes:
            logger.warning(
                (
                    "Skipping oversized response.create retry body request_id=%s request_log_id=%s "
                    "transport=%s bytes=%s max_bytes=%s"
                ),
                request_state.request_id,
                request_state.request_log_id,
                transport,
                payload_size,
                max_bytes,
            )
            return None
    return text_data


def _slim_response_create_payload_for_upstream(
    payload: dict[str, JsonValue],
    *,
    max_bytes: int,
) -> tuple[dict[str, JsonValue], dict[str, int] | None]:
    input_value = payload.get("input")
    if not isinstance(input_value, list) or not input_value:
        return payload, None

    input_items = cast(list[JsonValue], deepcopy(input_value))
    preserve_from = _response_create_recent_suffix_start(input_items)
    historical = input_items[:preserve_from]
    recent = input_items[preserve_from:]

    tool_outputs_slimmed = 0
    images_slimmed = 0

    slimmed_historical: list[JsonValue] = []
    for item in historical:
        (
            slimmed_item,
            item_tool_outputs_slimmed,
            item_images_slimmed,
        ) = _slim_historical_response_input_item(item)
        tool_outputs_slimmed += item_tool_outputs_slimmed
        images_slimmed += item_images_slimmed
        slimmed_historical.append(slimmed_item)

    candidate_payload = dict(payload)
    candidate_payload["input"] = slimmed_historical + recent

    if tool_outputs_slimmed == 0 and images_slimmed == 0:
        return payload, None

    return candidate_payload, {
        "historical_tool_outputs_slimmed": tool_outputs_slimmed,
        "historical_images_slimmed": images_slimmed,
    }


def _function_call_output_call_ids(input_items: list[JsonValue]) -> set[str]:
    call_ids: set[str] = set()
    for item in input_items:
        if not isinstance(item, dict) or item.get("type") != "function_call_output":
            continue
        call_id = item.get("call_id")
        if isinstance(call_id, str) and call_id:
            call_ids.add(call_id)
    return call_ids


def _missing_function_call_outputs_for_previous_response(
    input_items: list[JsonValue],
    *,
    pending_call_ids: list[str],
) -> list[str]:
    if not pending_call_ids:
        return []
    present_call_ids = _function_call_output_call_ids(input_items)
    return [call_id for call_id in pending_call_ids if call_id not in present_call_ids]


def _synthetic_interrupted_function_call_output(call_id: str) -> dict[str, JsonValue]:
    return {
        "type": "function_call_output",
        "call_id": call_id,
        "output": (
            "Tool call was not executed because the previous turn was interrupted before tool output was available."
        ),
    }


def _inject_missing_interrupted_function_call_outputs(
    input_items: list[JsonValue],
    *,
    missing_call_ids: list[str],
) -> list[JsonValue]:
    if not missing_call_ids:
        return input_items
    return [
        *[_synthetic_interrupted_function_call_output(call_id) for call_id in missing_call_ids],
        *input_items,
    ]


def _response_output_item_done_function_call_id(payload: dict[str, JsonValue] | None) -> str | None:
    if not isinstance(payload, dict) or payload.get("type") != "response.output_item.done":
        return None
    item = payload.get("item")
    if not isinstance(item, dict) or item.get("type") != "function_call":
        return None
    call_id = item.get("call_id")
    return call_id if isinstance(call_id, str) and call_id else None


def _response_create_too_large_error_envelope(
    actual_bytes: int,
    max_bytes: int,
) -> OpenAIErrorEnvelope:
    payload = openai_error(
        "payload_too_large",
        (
            "response.create is too large for upstream websocket "
            f"({actual_bytes} bytes > {max_bytes} bytes). "
            "Reduce historical images/screenshots or compact the thread."
        ),
        error_type="invalid_request_error",
    )
    payload["error"]["param"] = "input"
    return payload


def _response_create_recent_suffix_start(input_items: list[JsonValue]) -> int:
    last_user_index: int | None = None
    for index, item in enumerate(input_items):
        if not is_json_mapping(item):
            continue
        if item.get("role") == "user":
            last_user_index = index
    if last_user_index is not None:
        return last_user_index
    return 0


def _slim_historical_response_input_item(item: JsonValue) -> tuple[JsonValue, int, int]:
    if not is_json_mapping(item):
        return item, 0, 0

    item_mapping = dict(cast(dict[str, JsonValue], deepcopy(item)))
    tool_outputs_slimmed = 0
    images_slimmed = 0

    item_type = item_mapping.get("type")
    if item_type == "function_call_output":
        output = item_mapping.get("output")
        output_text = output if isinstance(output, str) else None
        if output_text is not None and _should_slim_historical_tool_output(output_text):
            item_mapping["output"] = _RESPONSE_CREATE_TOOL_OUTPUT_OMISSION_NOTICE.format(
                bytes=len(output_text.encode("utf-8"))
            )
            tool_outputs_slimmed += 1

    content = item_mapping.get("content")
    slimmed_content, content_images_slimmed = _slim_historical_response_content(content)
    if content_images_slimmed > 0:
        item_mapping["content"] = slimmed_content
        images_slimmed += content_images_slimmed

    if item_mapping.get("type") == "input_image" and _is_inline_image_reference(item_mapping.get("image_url")):
        return _response_create_inline_image_notice_item(), tool_outputs_slimmed, images_slimmed + 1

    return item_mapping, tool_outputs_slimmed, images_slimmed


def _slim_historical_response_content(content: JsonValue) -> tuple[JsonValue, int]:
    if is_json_mapping(content):
        return _slim_historical_response_content_part(content)
    if not isinstance(content, list):
        return content, 0

    slimmed_parts: list[JsonValue] = []
    images_slimmed = 0
    for part in content:
        slimmed_part, part_images_slimmed = _slim_historical_response_content_part(part)
        slimmed_parts.append(slimmed_part)
        images_slimmed += part_images_slimmed
    return slimmed_parts, images_slimmed


def _slim_historical_response_content_part(part: JsonValue) -> tuple[JsonValue, int]:
    if not is_json_mapping(part):
        return part, 0

    part_mapping = dict(cast(dict[str, JsonValue], deepcopy(part)))
    part_type = part_mapping.get("type")
    if part_type == "input_image" and _is_inline_image_reference(part_mapping.get("image_url")):
        return _response_create_inline_image_notice_part(), 1

    if part_type == "image_url":
        image_url_value = part_mapping.get("image_url")
        if is_json_mapping(image_url_value):
            image_url = image_url_value.get("url")
        else:
            image_url = image_url_value
        if _is_inline_image_reference(image_url):
            return _response_create_inline_image_notice_part(), 1

    return part_mapping, 0


def _response_create_inline_image_notice_part() -> dict[str, JsonValue]:
    return {"type": "input_text", "text": _RESPONSE_CREATE_IMAGE_OMISSION_NOTICE}


def _response_create_inline_image_notice_item() -> dict[str, JsonValue]:
    return {"role": "user", "content": [_response_create_inline_image_notice_part()]}


def _response_create_history_omission_notice_item(count: int) -> dict[str, JsonValue]:
    return {
        "role": "assistant",
        "content": [
            {
                "type": "output_text",
                "text": _RESPONSE_CREATE_HISTORY_OMISSION_NOTICE.format(count=count),
            }
        ],
    }


def _is_inline_image_reference(value: JsonValue) -> bool:
    return isinstance(value, str) and value.startswith("data:image/")


async def _inline_top_level_input_image_urls(
    payload: dict[str, JsonValue],
    session: ImageFetchSession,
    connect_timeout: float,
) -> dict[str, JsonValue]:
    input_value = payload.get("input")
    if not isinstance(input_value, list):
        return payload

    updated_input: list[JsonValue] = []
    changed = False
    for item in input_value:
        if not isinstance(item, dict) or item.get("type") != "input_image":
            updated_input.append(item)
            continue
        inline_content_images = _service_global_or("_inline_content_images", _inline_content_images)
        updated_item, item_changed = await inline_content_images(item, session, connect_timeout)
        updated_input.append(updated_item)
        changed = changed or item_changed
    if not changed:
        return payload

    updated_payload = dict(payload)
    updated_payload["input"] = updated_input
    return updated_payload


def _count_external_image_urls(payload: dict[str, JsonValue]) -> int:

    input_value = payload.get("input")
    if not isinstance(input_value, list):
        return 0
    count = 0
    for item in input_value:
        if not isinstance(item, dict):
            continue
        content_value = item.get("content")
        content_parts = content_value if isinstance(content_value, list) else [content_value]
        if item.get("type") == "input_image":
            content_parts = [item, *content_parts]
        for part in content_parts:
            if not isinstance(part, dict):
                continue
            if part.get("type") != "input_image":
                continue
            image_url = part.get("image_url")
            if isinstance(image_url, str) and image_url.startswith(("http://", "https://")):
                count += 1
    return count


def _should_slim_historical_tool_output(output: str) -> bool:
    return "data:image/" in output or len(output.encode("utf-8")) > 32 * 1024


def _enforce_response_create_size_limit(request_state: _WebSocketRequestState) -> None:
    request_text = request_state.request_text
    if not request_text:
        return

    payload_bytes = request_text.encode("utf-8")
    payload_size = len(payload_bytes)
    warn_bytes = _upstream_response_create_warn_bytes()
    max_bytes = _upstream_response_create_max_bytes()
    if payload_size > warn_bytes:
        logger.warning(
            (
                "Large response.create prepared request_id=%s request_log_id=%s "
                "transport=%s bytes=%s previous_response_id=%s"
            ),
            request_state.request_id,
            request_state.request_log_id,
            request_state.transport,
            payload_size,
            request_state.previous_response_id,
        )
    if payload_size <= max_bytes:
        return

    payload = _response_create_too_large_error_envelope(payload_size, max_bytes)
    error = payload["error"]
    write_response_create_dump = _service_global_or("_write_response_create_dump", _write_response_create_dump)
    write_response_create_dump(
        request_state,
        account_id_value=None,
        error_code=cast(str, error.get("code") or "payload_too_large"),
        error_message=error.get("message"),
        log_prefix="guarded",
    )
    raise ProxyResponseError(
        413,
        payload,
        failure_phase="validation",
        failure_detail=f"response.create_bytes={payload_size}",
    )


def _response_create_client_metadata(
    payload: Mapping[str, JsonValue],
    *,
    headers: Mapping[str, str],
) -> Mapping[str, JsonValue] | None:
    raw_value = payload.get("client_metadata")
    client_metadata: dict[str, JsonValue] = {}
    if is_json_mapping(raw_value):
        for key, value in raw_value.items():
            if isinstance(key, str):
                client_metadata[key] = value

    normalized_headers = {key.lower(): value for key, value in headers.items()}
    turn_metadata = normalized_headers.get("x-codex-turn-metadata")
    if isinstance(turn_metadata, str) and turn_metadata.strip():
        client_metadata.setdefault("x-codex-turn-metadata", turn_metadata)

    return client_metadata or None
