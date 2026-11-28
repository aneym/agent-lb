from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass

from pydantic import TypeAdapter, ValidationError

from app.core.anthropic.models import (
    AnthropicContentBlockDeltaEvent,
    AnthropicContentBlockStartEvent,
    AnthropicErrorEvent,
    AnthropicEvent,
    AnthropicMessageDeltaEvent,
    AnthropicMessageStartEvent,
    AnthropicStopDetails,
    AnthropicUsage,
    merge_usage_values,
)
from app.core.utils.sse import extract_sse_data, parse_sse_data_json

_EVENT_ADAPTER = TypeAdapter(AnthropicEvent)


@dataclass(frozen=True, slots=True)
class AnthropicResponseMetadata:
    response_model: str | None = None
    stop_reason: str | None = None
    stop_details_category: str | None = None
    stop_details_explanation: str | None = None


def _clean_str(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _stop_details_fields(stop_details: object) -> tuple[str | None, str | None]:
    if isinstance(stop_details, AnthropicStopDetails):
        return _clean_str(stop_details.category), _clean_str(stop_details.explanation)
    if isinstance(stop_details, dict):
        return _clean_str(stop_details.get("category")), _clean_str(stop_details.get("explanation"))
    return None, None


def merge_response_metadata(
    left: AnthropicResponseMetadata,
    right: AnthropicResponseMetadata,
) -> AnthropicResponseMetadata:
    return AnthropicResponseMetadata(
        response_model=right.response_model or left.response_model,
        stop_reason=right.stop_reason or left.stop_reason,
        stop_details_category=right.stop_details_category or left.stop_details_category,
        stop_details_explanation=right.stop_details_explanation or left.stop_details_explanation,
    )


def metadata_from_event(event: AnthropicEvent) -> AnthropicResponseMetadata:
    if isinstance(event, AnthropicMessageStartEvent):
        category, explanation = _stop_details_fields(event.message.stop_details)
        return AnthropicResponseMetadata(
            response_model=_clean_str(event.message.model),
            stop_reason=_clean_str(event.message.stop_reason),
            stop_details_category=category,
            stop_details_explanation=explanation,
        )
    if isinstance(event, AnthropicMessageDeltaEvent):
        details = event.delta.stop_details or event.stop_details
        category, explanation = _stop_details_fields(details)
        return AnthropicResponseMetadata(
            stop_reason=_clean_str(event.delta.stop_reason),
            stop_details_category=category,
            stop_details_explanation=explanation,
        )
    return AnthropicResponseMetadata()


def _parse_usage_dict(value: object) -> AnthropicUsage | None:
    if not isinstance(value, dict):
        return None
    try:
        return AnthropicUsage.model_validate(value)
    except ValidationError:
        return None


def _metadata_and_usage_from_raw_sse(block: str) -> tuple[AnthropicUsage | None, AnthropicResponseMetadata]:
    payload = parse_sse_data_json(block)
    if not isinstance(payload, dict):
        return None, AnthropicResponseMetadata()
    event_type = payload.get("type")
    if event_type == "message_start":
        message = payload.get("message")
        if not isinstance(message, dict):
            return None, AnthropicResponseMetadata()
        category, explanation = _stop_details_fields(message.get("stop_details"))
        return _parse_usage_dict(message.get("usage")), AnthropicResponseMetadata(
            response_model=_clean_str(message.get("model")),
            stop_reason=_clean_str(message.get("stop_reason")),
            stop_details_category=category,
            stop_details_explanation=explanation,
        )
    if event_type == "message_delta":
        delta = payload.get("delta")
        delta_dict = delta if isinstance(delta, dict) else {}
        details = delta_dict.get("stop_details")
        if details is None:
            details = payload.get("stop_details")
        category, explanation = _stop_details_fields(details)
        return _parse_usage_dict(payload.get("usage")), AnthropicResponseMetadata(
            response_model=_clean_str(payload.get("model")),
            stop_reason=_clean_str(delta_dict.get("stop_reason")),
            stop_details_category=category,
            stop_details_explanation=explanation,
        )
    return None, AnthropicResponseMetadata()


def parse_sse_event(event_block: str) -> AnthropicEvent | None:
    payload = parse_sse_data_json(event_block)
    if payload is None:
        return None
    try:
        return _EVENT_ADAPTER.validate_python(payload)
    except ValidationError:
        return None


def usage_from_events(events: Iterable[AnthropicEvent]) -> AnthropicUsage | None:
    usage: AnthropicUsage | None = None
    for event in events:
        if isinstance(event, AnthropicMessageStartEvent):
            usage = merge_usage_values(usage, event.message.usage)
        elif isinstance(event, AnthropicMessageDeltaEvent):
            usage = merge_usage_values(usage, event.usage)
    return usage


def parse_json_response(raw: bytes) -> tuple[AnthropicUsage | None, AnthropicResponseMetadata]:
    if not raw:
        return None, AnthropicResponseMetadata()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, AnthropicResponseMetadata()
    if not isinstance(payload, dict):
        return None, AnthropicResponseMetadata()
    category, explanation = _stop_details_fields(payload.get("stop_details"))
    metadata = AnthropicResponseMetadata(
        response_model=_clean_str(payload.get("model")),
        stop_reason=_clean_str(payload.get("stop_reason")),
        stop_details_category=category,
        stop_details_explanation=explanation,
    )
    return _parse_usage_dict(payload.get("usage")), metadata


_EVENT_NAME_RE = re.compile(r"^event:[ \t]*([^\n]*?)[ \t]*$", re.MULTILINE)
_SKIPPED_EVENTS = frozenset(("content_block_start", "content_block_delta", "content_block_stop", "ping"))
_PAYLOAD_TYPE_RE = re.compile(r'^\{\s*"type"\s*:\s*"([^"\\]*)"')
_FIRST_OUTPUT_EVENT_RE = re.compile(r"^event:[ \t]*content_block_(?:start|delta)[ \t]*$", re.MULTILINE)


def collect_response_from_chunk(
    text_buffer: str,
    chunk: bytes,
    usage: AnthropicUsage | None,
    metadata: AnthropicResponseMetadata | None = None,
) -> tuple[str, AnthropicUsage | None, AnthropicErrorEvent | None, bool, AnthropicResponseMetadata]:
    error_event: AnthropicErrorEvent | None = None
    saw_output = False
    resolved_metadata = metadata if metadata is not None else AnthropicResponseMetadata()
    text_buffer += chunk.decode("utf-8", errors="replace")
    normalized = text_buffer.replace("\r\n", "\n")
    while "\n\n" in normalized:
        block, normalized = normalized.split("\n\n", 1)

        if _FIRST_OUTPUT_EVENT_RE.search(block):
            saw_output = True
        names = _EVENT_NAME_RE.findall(block)
        if len(names) == 1 and names[0] in _SKIPPED_EVENTS:
            payload = extract_sse_data(block)
            payload_type = _PAYLOAD_TYPE_RE.match(payload) if payload is not None else None
            if payload_type is not None and payload_type[1] == names[0]:
                continue
        event = parse_sse_event(block)
        if isinstance(event, AnthropicContentBlockStartEvent | AnthropicContentBlockDeltaEvent):
            saw_output = True
        if event is None:
            raw_usage, raw_metadata = _metadata_and_usage_from_raw_sse(block)
            usage = merge_usage_values(usage, raw_usage)
            resolved_metadata = merge_response_metadata(resolved_metadata, raw_metadata)
            continue
        if isinstance(event, AnthropicErrorEvent):
            error_event = event
            continue
        resolved_metadata = merge_response_metadata(resolved_metadata, metadata_from_event(event))
        event_usage = getattr(event, "usage", None)
        message = getattr(event, "message", None)
        if event_usage is None and message is not None:
            event_usage = message.usage
        usage = merge_usage_values(usage, event_usage)
    return normalized, usage, error_event, saw_output, resolved_metadata


def collect_usage_from_chunk(
    text_buffer: str,
    chunk: bytes,
    usage: AnthropicUsage | None,
) -> tuple[str, AnthropicUsage | None, AnthropicErrorEvent | None, bool]:
    normalized, updated_usage, error_event, saw_output, _ = collect_response_from_chunk(text_buffer, chunk, usage)
    return normalized, updated_usage, error_event, saw_output
