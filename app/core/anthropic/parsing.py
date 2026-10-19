from __future__ import annotations

import re
from collections.abc import Iterable

from pydantic import TypeAdapter, ValidationError

from app.core.anthropic.models import (
    AnthropicContentBlockDeltaEvent,
    AnthropicContentBlockStartEvent,
    AnthropicErrorEvent,
    AnthropicEvent,
    AnthropicMessageDeltaEvent,
    AnthropicMessageStartEvent,
    AnthropicUsage,
    merge_usage_values,
)
from app.core.utils.sse import parse_sse_data_json

_EVENT_ADAPTER = TypeAdapter(AnthropicEvent)


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


_EVENT_NAME_RE = re.compile(r"^event:[ \t]*([^\n]*?)[ \t]*$", re.MULTILINE)
_SKIPPED_EVENTS = frozenset(("content_block_start", "content_block_delta", "content_block_stop", "ping"))
_FIRST_OUTPUT_EVENT_RE = re.compile(r"^event:[ \t]*content_block_(?:start|delta)[ \t]*$", re.MULTILINE)


def collect_usage_from_chunk(
    text_buffer: str,
    chunk: bytes,
    usage: AnthropicUsage | None,
) -> tuple[str, AnthropicUsage | None, AnthropicErrorEvent | None, bool]:

    error_event: AnthropicErrorEvent | None = None
    saw_output = False
    text = chunk.decode("utf-8", errors="replace")
    if text_buffer.endswith("\r") and text.startswith("\n"):
        text_buffer = text_buffer[:-1]
    normalized = text_buffer + text.replace("\r\n", "\n")
    while "\n\n" in normalized:
        block, normalized = normalized.split("\n\n", 1)

        if _FIRST_OUTPUT_EVENT_RE.search(block):
            saw_output = True
        names = _EVENT_NAME_RE.findall(block)
        if len(names) == 1 and names[0] in _SKIPPED_EVENTS:
            continue
        event = parse_sse_event(block)
        if isinstance(event, AnthropicContentBlockStartEvent | AnthropicContentBlockDeltaEvent):
            saw_output = True
        if event is None:
            continue
        if isinstance(event, AnthropicErrorEvent):
            error_event = event
            continue
        event_usage = getattr(event, "usage", None)
        message = getattr(event, "message", None)
        if event_usage is None and message is not None:
            event_usage = message.usage
        usage = merge_usage_values(usage, event_usage)
    return normalized, usage, error_event, saw_output
