from __future__ import annotations

from collections.abc import Iterable

from pydantic import TypeAdapter, ValidationError

from app.core.anthropic.models import (
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
