from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from app.core.errors import response_failed_event
from app.core.types import JsonValue
from app.core.utils.json_guards import is_json_mapping
from app.core.utils.sse import format_sse_event
from app.modules.proxy.constants import (
    INDEXED_PRE_CREATED_LIFECYCLE_EVENT_TYPES,
    PUBLIC_RESPONSE_STREAM_TERMINAL_TYPES,
    PUBLIC_RESPONSE_TEXT_PART_TYPES,
    REPLAYABLE_PUBLIC_PRE_CREATED_EVENT_TYPES,
)
from app.modules.proxy.contract_errors import public_contract_error_message
from app.modules.proxy.public_payloads import synthetic_response_created_envelope
from app.modules.proxy.response_errors import default_error_envelope, parse_event_error_envelope


def should_buffer_public_pre_created_event(event_type: str) -> bool:
    return (
        event_type.startswith("response.")
        and event_type != "response.created"
        and event_type not in PUBLIC_RESPONSE_STREAM_TERMINAL_TYPES
    )


def public_response_failed_event_blocks(error_kind: str, *, include_created: bool) -> list[str]:
    failed_payload = cast(
        dict[str, JsonValue],
        response_failed_event(
            error_kind,
            public_contract_error_message(error_kind),
            response_id=f"resp_{error_kind}",
        ),
    )
    blocks: list[str] = []
    if include_created:
        synthetic_created = synthetic_response_created_envelope(failed_payload)
        if synthetic_created is not None:
            blocks.append(format_sse_event(synthetic_created))
    blocks.append(format_sse_event(failed_payload))
    return blocks


def public_response_failed_event_blocks_from_error(
    payload: dict[str, JsonValue],
    *,
    include_created: bool,
) -> list[str]:
    envelope = parse_event_error_envelope(payload)
    error = envelope.error
    if error is None:
        error = default_error_envelope().error
    assert error is not None
    failed_payload = cast(
        dict[str, JsonValue],
        response_failed_event(
            error.code or "upstream_error",
            error.message or "Upstream error",
            error.type or "server_error",
            response_id=f"resp_{error.code or 'upstream_error'}",
            error_param=error.param,
        ),
    )
    blocks: list[str] = []
    if include_created:
        synthetic_created = synthetic_response_created_envelope(failed_payload)
        if synthetic_created is not None:
            blocks.append(format_sse_event(synthetic_created))
    blocks.append(format_sse_event(failed_payload))
    return blocks


def pre_created_buffer_has_indexed_lifecycle(payloads: list[dict[str, JsonValue]]) -> bool:
    for payload in payloads:
        event_type = payload.get("type")
        if isinstance(event_type, str) and event_type in INDEXED_PRE_CREATED_LIFECYCLE_EVENT_TYPES:
            return True
        if isinstance(payload.get("output_index"), int) or isinstance(payload.get("item_id"), str):
            return True
    return False


def format_buffered_pre_created_payloads(
    payloads: list[dict[str, JsonValue]],
    *,
    response_id: str | None,
    seen_text_delta_keys: set[tuple[str | None, int | None]],
) -> list[str]:
    formatted: list[str] = []
    if not payloads:
        return formatted

    item_id = synthetic_pre_created_item_id(response_id)
    output_index = 0
    content_index = 0
    text_item_opened = False
    text_parts: list[str] = []
    final_text: str | None = None

    def open_text_item(sequence_number: int) -> None:
        nonlocal text_item_opened
        if text_item_opened:
            return
        output_item_added = cast(
            dict[str, JsonValue],
            {
                "type": "response.output_item.added",
                "sequence_number": sequence_number,
                "output_index": output_index,
                "item": {
                    "id": item_id,
                    "type": "message",
                    "role": "assistant",
                    "status": "in_progress",
                    "content": [],
                },
            },
        )
        formatted.append(format_sse_event(output_item_added))
        content_part_added = cast(
            dict[str, JsonValue],
            {
                "type": "response.content_part.added",
                "sequence_number": sequence_number,
                "output_index": output_index,
                "content_index": content_index,
                "item_id": item_id,
                "part": {"type": "output_text", "text": ""},
            },
        )
        formatted.append(format_sse_event(content_part_added))
        text_item_opened = True

    sequence_number = 0
    for payload in payloads:
        event_type = payload.get("type")
        if not isinstance(event_type, str) or event_type not in REPLAYABLE_PUBLIC_PRE_CREATED_EVENT_TYPES:
            continue
        sequence_number = payload_sequence_number(payload, sequence_number + 1)
        if event_type in {"response.output_text.delta", "response.refusal.delta"}:
            delta = payload.get("delta")
            if not isinstance(delta, str):
                continue
            open_text_item(sequence_number)
            text_parts.append(delta)
            normalized = dict(payload)
            normalized["sequence_number"] = sequence_number
            normalized["output_index"] = output_index
            normalized["content_index"] = content_index
            normalized["item_id"] = item_id
            normalized.setdefault("logprobs", [])
            formatted.append(format_sse_event(normalized))
            seen_text_delta_keys.add((item_id, output_index))
            continue
        if event_type in {"response.output_text.done", "response.refusal.done"}:
            text = payload.get("text")
            if not isinstance(text, str):
                text = "".join(text_parts)
            open_text_item(sequence_number)
            final_text = text
            normalized = dict(payload)
            normalized["sequence_number"] = sequence_number
            normalized["output_index"] = output_index
            normalized["content_index"] = content_index
            normalized["item_id"] = item_id
            normalized.setdefault("logprobs", [])
            formatted.append(format_sse_event(normalized))
            continue
        part = payload.get("part")
        if is_json_mapping(part) and part.get("type") in PUBLIC_RESPONSE_TEXT_PART_TYPES:
            text = part.get("text")
            if isinstance(text, str):
                final_text = text
            open_text_item(sequence_number)
            normalized = dict(payload)
            normalized["sequence_number"] = sequence_number
            normalized["output_index"] = output_index
            normalized["content_index"] = content_index
            normalized["item_id"] = item_id
            formatted.append(format_sse_event(normalized))
        else:
            formatted.append(format_sse_event(payload))

    if text_item_opened:
        seen_text_delta_keys.add((None, output_index))
        text = final_text if final_text is not None else "".join(text_parts)
        output_item_done = cast(
            dict[str, JsonValue],
            {
                "type": "response.output_item.done",
                "sequence_number": sequence_number + 1,
                "output_index": output_index,
                "item": {
                    "id": item_id,
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": text}],
                },
            },
        )
        formatted.append(format_sse_event(output_item_done))
    return formatted


def payload_sequence_number(payload: Mapping[str, JsonValue], fallback: int) -> int:
    sequence_number = payload.get("sequence_number")
    return sequence_number if isinstance(sequence_number, int) else fallback


def response_id_from_event_payload(payload: Mapping[str, JsonValue]) -> str | None:
    response = payload.get("response")
    if not is_json_mapping(response):
        return None
    response_id = response.get("id")
    return response_id if isinstance(response_id, str) and response_id else None


def synthetic_pre_created_item_id(response_id: str | None) -> str:
    if response_id:
        return f"msg_{response_id}_precreated"
    return "msg_precreated"
