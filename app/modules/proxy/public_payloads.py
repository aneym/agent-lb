from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from app.core.errors import PREVIOUS_RESPONSE_STREAM_INCOMPLETE_MESSAGE, response_failed_event
from app.core.types import JsonValue
from app.core.utils.json_guards import is_json_mapping
from app.modules.proxy.constants import PUBLIC_RESPONSE_OUTPUT_ITEM_TYPES, PUBLIC_RESPONSE_TEXT_PART_TYPES
from app.modules.proxy.contract_errors import public_contract_error_message, record_public_contract_violation
from app.modules.proxy.output_items import merge_collected_output_items
from app.modules.proxy.response_errors import is_previous_response_not_found_public_error, parse_event_error_envelope


def normalize_public_stream_payload(
    payload: dict[str, JsonValue],
    *,
    enforce_openai_sdk_contract: bool = True,
) -> tuple[dict[str, JsonValue] | None, str | None]:
    event_type = payload.get("type")

    if enforce_openai_sdk_contract and isinstance(event_type, str) and event_type.startswith("codex."):
        return None, None
    if event_type == "error":
        parsed_error = parse_event_error_envelope(payload)
        if is_previous_response_not_found_public_error(parsed_error.error):
            return (
                cast(
                    dict[str, JsonValue],
                    response_failed_event(
                        "stream_incomplete",
                        PREVIOUS_RESPONSE_STREAM_INCOMPLETE_MESSAGE,
                    ),
                ),
                None,
            )
        return payload, None
    if event_type in ("response.completed", "response.incomplete"):
        if not enforce_openai_sdk_contract:
            return payload, None
        response = payload.get("response")
        if not is_json_mapping(response):
            return (
                cast(
                    dict[str, JsonValue],
                    response_failed_event(
                        "invalid_json",
                        public_contract_error_message("invalid_json"),
                    ),
                ),
                "invalid_json",
            )
        normalized_response, violation_kind = normalize_public_response_mapping(response)
        if normalized_response is None:
            error_kind = violation_kind or "invalid_output_item"
            return (
                cast(
                    dict[str, JsonValue],
                    response_failed_event(
                        error_kind,
                        public_contract_error_message(error_kind),
                    ),
                ),
                error_kind,
            )
        normalized_payload = dict(payload)
        normalized_payload["response"] = normalized_response
        return normalized_payload, violation_kind
    if event_type in ("response.output_item.added", "response.output_item.done"):
        if not enforce_openai_sdk_contract:
            return payload, None
        item = payload.get("item")
        if not is_json_mapping(item):
            return None, "invalid_output_item"
        normalized_item = normalize_public_output_item(item)
        if normalized_item is None:
            return None, "invalid_output_item"
        normalized_payload = dict(payload)
        normalized_payload["item"] = normalized_item
        violation_kind = None
        item_type = item.get("type")
        if isinstance(item_type, str) and not is_public_passthrough_output_item_type(item_type):
            violation_kind = "invalid_output_item"
        return normalized_payload, violation_kind
    return payload, None


def synthetic_response_created_envelope(
    payload: Mapping[str, JsonValue],
) -> dict[str, JsonValue] | None:

    response = payload.get("response")
    if not is_json_mapping(response):
        return None
    created_envelope: dict[str, JsonValue] = dict(response)
    created_envelope["status"] = "in_progress"
    created_envelope["output"] = []
    synthetic: dict[str, JsonValue] = {
        "type": "response.created",
        "response": created_envelope,
    }
    sequence_number = payload.get("sequence_number")
    if isinstance(sequence_number, int):
        synthetic["sequence_number"] = sequence_number
    return synthetic


def synthetic_text_delta_events(
    payload: Mapping[str, JsonValue],
    seen_text_delta_keys: set[tuple[str | None, int | None]],
) -> list[dict[str, JsonValue]]:
    event_type = payload.get("type")
    if event_type == "response.output_item.done":
        output_index = payload.get("output_index")
        item = payload.get("item")
        if isinstance(output_index, int) and is_json_mapping(item):
            synthetic = synthetic_text_delta_for_output_item(output_index, item, seen_text_delta_keys)
            return [synthetic] if synthetic is not None else []
    if event_type not in {"response.completed", "response.incomplete"}:
        return []
    response = payload.get("response")
    if not is_json_mapping(response):
        return []
    output = response.get("output")
    if not isinstance(output, list):
        return []

    synthetic_events: list[dict[str, JsonValue]] = []
    for output_index, item in enumerate(output):
        if not is_json_mapping(item):
            continue
        synthetic = synthetic_text_delta_for_output_item(output_index, item, seen_text_delta_keys)
        if synthetic is not None:
            synthetic_events.append(synthetic)
    return synthetic_events


def synthetic_text_delta_for_output_item(
    output_index: int,
    item: Mapping[str, JsonValue],
    seen_text_delta_keys: set[tuple[str | None, int | None]],
) -> dict[str, JsonValue] | None:
    normalized_item = normalize_public_output_item(item)
    if normalized_item is None:
        return None
    text = extract_public_output_item_text(normalized_item)
    if text is None:
        return None
    key = output_item_stream_key(output_index, normalized_item)
    if seen_text_delta_for_output_item(key, seen_text_delta_keys):
        return None
    seen_text_delta_keys.add(key)

    event: dict[str, JsonValue] = {
        "type": "response.output_text.delta",
        "output_index": output_index,
        "content_index": 0,
        "delta": text,
    }
    item_id = normalized_item.get("id")
    if isinstance(item_id, str) and item_id:
        event["item_id"] = item_id
    return event


def text_delta_stream_key(payload: Mapping[str, JsonValue]) -> tuple[str | None, int | None]:
    item_id = payload.get("item_id")
    output_index = payload.get("output_index")
    return (
        item_id if isinstance(item_id, str) and item_id else None,
        output_index if isinstance(output_index, int) else None,
    )


def output_item_stream_key(
    output_index: int,
    item: Mapping[str, JsonValue],
) -> tuple[str | None, int | None]:
    item_id = item.get("id")
    return (item_id if isinstance(item_id, str) and item_id else None, output_index)


def seen_text_delta_for_output_item(
    key: tuple[str | None, int | None],
    seen_text_delta_keys: set[tuple[str | None, int | None]],
) -> bool:
    item_id, output_index = key
    return any(
        candidate in seen_text_delta_keys
        for candidate in (
            key,
            (item_id, None) if item_id is not None else None,
            (None, output_index) if output_index is not None else None,
            (None, None),
        )
        if candidate is not None
    )


def normalize_public_response_mapping(
    response: Mapping[str, JsonValue],
    output_items: dict[int, dict[str, JsonValue]] | None = None,
) -> tuple[dict[str, JsonValue] | None, str | None]:
    merged = merge_collected_output_items(response, output_items or {})
    output = merged.get("output")
    if not isinstance(output, list):
        return merged, None
    normalized_output: list[JsonValue] = []
    dropped_items = 0
    for item in output:
        if not is_json_mapping(item):
            dropped_items += 1
            continue
        normalized_item = normalize_public_output_item(item)
        if normalized_item is None:
            dropped_items += 1
            continue
        normalized_output.append(normalized_item)
    if output and not normalized_output:
        record_public_contract_violation("invalid_output_item")
        return None, "invalid_output_item"
    normalized = dict(merged)
    normalized["output"] = normalized_output
    if dropped_items:
        record_public_contract_violation("invalid_output_item")
        return normalized, "invalid_output_item"
    return normalized, None


def normalize_public_output_item(item: Mapping[str, JsonValue]) -> dict[str, JsonValue] | None:
    item_type = item.get("type")
    if isinstance(item_type, str) and is_public_passthrough_output_item_type(item_type):
        return dict(item)
    text_value = extract_public_output_item_text(item)
    if text_value is None:
        return None
    normalized: dict[str, JsonValue] = {
        "type": "message",
        "role": "assistant",
        "status": item.get("status") if isinstance(item.get("status"), str) else "completed",
        "content": [{"type": "output_text", "text": text_value}],
    }
    item_id = item.get("id")
    if isinstance(item_id, str) and item_id:
        normalized["id"] = item_id
    return normalized


def is_public_passthrough_output_item_type(item_type: str) -> bool:
    if item_type in PUBLIC_RESPONSE_OUTPUT_ITEM_TYPES:
        return True
    return item_type.endswith("_call") or item_type.endswith("_call_output")


def extract_public_output_item_text(item: Mapping[str, JsonValue]) -> str | None:
    direct_text = item.get("text")
    if isinstance(direct_text, str) and direct_text:
        return direct_text
    content = item.get("content")
    if is_json_mapping(content):
        content_parts: list[Mapping[str, JsonValue]] = [content]
    elif isinstance(content, list):
        content_parts = [part for part in content if is_json_mapping(part)]
    else:
        content_parts = []
    parts: list[str] = []
    for part in content_parts:
        part_type = part.get("type")
        if isinstance(part_type, str) and part_type in PUBLIC_RESPONSE_TEXT_PART_TYPES:
            text = part.get("text")
            if isinstance(text, str) and text:
                parts.append(text)
                continue
        text = part.get("text")
        if isinstance(text, str) and text:
            parts.append(text)
    if parts:
        return "".join(parts)
    summary = item.get("summary")
    if isinstance(summary, str) and summary:
        return summary
    return None
