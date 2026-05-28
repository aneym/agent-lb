from __future__ import annotations

import json

import pytest

from app.core.anthropic.model_registry import AnthropicModelRegistry, parse_model_list_payload
from app.core.anthropic.models import (
    AnthropicContentBlockStartEvent,
    AnthropicMessageRequest,
    AnthropicToolUseBlock,
    AnthropicUsage,
)
from app.core.anthropic.parsing import parse_sse_event, usage_from_events
from app.core.anthropic.pricing import (
    DEFAULT_PRICING_MODELS,
    calculate_anthropic_cost_breakdown,
    get_pricing_for_model,
)

pytestmark = pytest.mark.unit


def _sse_block(event_type: str, payload: dict[str, object]) -> str:
    data = json.dumps(payload, separators=(",", ":"))
    return f"event: {event_type}\ndata: {data}"


ANTHROPIC_SSE_STREAM = "\n\n".join(
    [
        _sse_block(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": "msg_01",
                    "type": "message",
                    "role": "assistant",
                    "model": "claude-sonnet-4-20250514",
                    "content": [],
                    "usage": {
                        "input_tokens": 1000,
                        "cache_creation_input_tokens": 100,
                        "cache_read_input_tokens": 200,
                    },
                },
            },
        ),
        _sse_block(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
        ),
        _sse_block(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "I'll inspect the file."},
            },
        ),
        _sse_block("content_block_stop", {"type": "content_block_stop", "index": 0}),
        _sse_block(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 1,
                "content_block": {
                    "type": "tool_use",
                    "id": "toolu_01",
                    "name": "Read",
                    "input": {"file_path": "/tmp/example.py", "limit": 25},
                },
            },
        ),
        _sse_block("content_block_stop", {"type": "content_block_stop", "index": 1}),
        _sse_block(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "tool_use", "stop_sequence": None},
                "usage": {"output_tokens": 42},
            },
        ),
        _sse_block("message_stop", {"type": "message_stop"}),
    ]
)


def _event_blocks(stream: str) -> list[str]:
    return [block for block in stream.split("\n\n") if block.strip()]


def test_parse_messages_sse_stream_yields_usage_and_tool_use_block():
    events = [event for block in _event_blocks(ANTHROPIC_SSE_STREAM) if (event := parse_sse_event(block)) is not None]

    assert [event.type for event in events] == [
        "message_start",
        "content_block_start",
        "content_block_delta",
        "content_block_stop",
        "content_block_start",
        "content_block_stop",
        "message_delta",
        "message_stop",
    ]

    usage = usage_from_events(events)
    assert usage == AnthropicUsage(
        input_tokens=1000,
        output_tokens=42,
        cache_creation_input_tokens=100,
        cache_read_input_tokens=200,
    )

    tool_event = events[4]
    assert isinstance(tool_event, AnthropicContentBlockStartEvent)
    assert isinstance(tool_event.content_block, AnthropicToolUseBlock)
    assert tool_event.content_block.id == "toolu_01"
    assert tool_event.content_block.name == "Read"
    assert tool_event.content_block.input == {
        "file_path": "/tmp/example.py",
        "limit": 25,
    }


def test_pricing_computes_cache_aware_cost_for_sonnet():
    usage = AnthropicUsage(
        input_tokens=1000,
        output_tokens=400,
        cache_creation_input_tokens=100,
        cache_read_input_tokens=200,
    )

    breakdown = calculate_anthropic_cost_breakdown(usage, DEFAULT_PRICING_MODELS["claude-sonnet-4"])

    assert breakdown is not None
    assert breakdown.input_usd == pytest.approx(0.003)
    assert breakdown.cache_creation_input_usd == pytest.approx(0.000375)
    assert breakdown.cache_read_input_usd == pytest.approx(0.00006)
    assert breakdown.output_usd == pytest.approx(0.006)
    assert breakdown.total_usd == pytest.approx(0.009435)


def test_pricing_resolves_versioned_model_alias():
    resolved = get_pricing_for_model("claude-sonnet-4-20250514")

    assert resolved is not None
    model, price = resolved
    assert model == "claude-sonnet-4"
    assert price.output_per_1m == 15.0


def test_model_registry_parses_anthropic_model_list_payload():
    payload = {
        "data": [
            {
                "id": "claude-sonnet-4-20250514",
                "type": "model",
                "display_name": "Claude Sonnet 4",
                "created_at": "2025-05-14T00:00:00Z",
            },
            {
                "id": "claude-opus-4-1-20250805",
                "type": "model",
                "display_name": "Claude Opus 4.1",
                "created_at": "2025-08-05T00:00:00Z",
            },
        ],
        "first_id": "claude-sonnet-4-20250514",
        "last_id": "claude-opus-4-1-20250805",
        "has_more": False,
    }

    model_list = parse_model_list_payload(payload)
    assert [model.id for model in model_list.data] == [
        "claude-sonnet-4-20250514",
        "claude-opus-4-1-20250805",
    ]
    assert model_list.has_more is False

    registry = AnthropicModelRegistry()
    snapshot = registry.update_from_payload(payload)
    assert set(snapshot.models) == {"claude-sonnet-4-20250514", "claude-opus-4-1-20250805"}
    assert registry.get_models_with_fallback()["claude-sonnet-4-20250514"].display_name == "Claude Sonnet 4"


def test_message_request_model_accepts_tools_and_system_prompt():
    request = AnthropicMessageRequest.model_validate(
        {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 1024,
            "system": "You are Claude Code, Anthropic's official CLI for Claude.",
            "messages": [{"role": "user", "content": "inspect the repo"}],
            "tools": [
                {
                    "name": "Read",
                    "description": "Read a file",
                    "input_schema": {
                        "type": "object",
                        "properties": {"file_path": {"type": "string"}},
                        "required": ["file_path"],
                    },
                }
            ],
            "stream": True,
        }
    )

    assert request.system == "You are Claude Code, Anthropic's official CLI for Claude."
    assert request.tools is not None
    assert request.tools[0].name == "Read"
