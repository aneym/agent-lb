from __future__ import annotations

import json

import pytest

from app.core.anthropic.models import AnthropicMessageRequest
from app.modules.proxy.claude_codex_bridge import (
    CCDEX_MODEL,
    claude_to_responses,
    estimate_claude_input_tokens,
    responses_to_claude_sse,
)

pytestmark = pytest.mark.unit


def _request(**overrides: object) -> AnthropicMessageRequest:
    payload: dict[str, object] = {
        "model": "caller-controlled-model",
        "max_tokens": 4096,
        "messages": [{"role": "user", "content": "hello"}],
        "stream": True,
    }
    payload.update(overrides)
    return AnthropicMessageRequest.model_validate(payload)


@pytest.mark.parametrize("effort", ["low", "medium", "high", "xhigh"])
def test_request_translation_propagates_supported_effort(effort: str) -> None:
    translated = claude_to_responses(_request(output_config={"effort": effort}))

    assert translated.reasoning is not None and translated.reasoning.effort == effort
    assert translated.service_tier == "priority"
    assert translated.to_payload()["service_tier"] == "priority"


def test_request_translation_defaults_invalid_effort_to_high() -> None:
    translated = claude_to_responses(_request(output_config={"effort": "max"}))

    assert translated.reasoning is not None and translated.reasoning.effort == "high"


def test_request_translation_locks_sol_high_priority_and_maps_tools() -> None:
    translated = claude_to_responses(
        _request(
            system=[{"type": "text", "text": "You are Claude Code."}],
            messages=[
                {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": "call_1", "name": "Read", "input": {"path": "README.md"}}],
                },
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": "contents"}]},
            ],
            tools=[{"name": "Read", "description": "Read a file", "input_schema": {"type": "object"}}],
            tool_choice={"type": "tool", "name": "Read"},
        )
    )

    assert translated.model == CCDEX_MODEL
    assert translated.reasoning is not None and translated.reasoning.effort == "high"
    assert translated.service_tier == "priority"
    assert translated.to_payload()["service_tier"] == "priority"
    assert translated.store is False
    assert translated.include == ["reasoning.encrypted_content"]
    assert translated.input == [
        {"type": "function_call", "call_id": "call_1", "name": "Read", "arguments": '{"path":"README.md"}'},
        {"type": "function_call_output", "call_id": "call_1", "output": "contents"},
    ]
    assert translated.tools[0] == {
        "type": "function",
        "name": "Read",
        "description": "Read a file",
        "parameters": {"type": "object"},
        "strict": False,
    }
    assert translated.tool_choice == {"type": "function", "name": "Read"}


@pytest.mark.asyncio
async def test_response_translation_emits_native_text_stream() -> None:
    async def source():
        yield 'data: {"type":"response.created","response":{"id":"resp_123","model":"gpt-5.6-sol"}}\n\n'
        yield 'data: {"type":"response.output_text.delta","delta":"hello"}\n\n'
        yield 'data: {"type":"response.completed","response":{"usage":{"input_tokens":12,"output_tokens":3}}}\n\n'

    frames = [frame async for frame in responses_to_claude_sse(source())]
    payloads = [json.loads(frame.split("data: ", 1)[1]) for frame in frames]

    assert [payload["type"] for payload in payloads] == [
        "message_start",
        "content_block_start",
        "content_block_delta",
        "content_block_stop",
        "message_delta",
        "message_stop",
    ]
    assert payloads[2]["delta"] == {"type": "text_delta", "text": "hello"}
    assert payloads[4]["usage"] == {"input_tokens": 12, "output_tokens": 3}
    assert payloads[4]["delta"]["stop_reason"] == "end_turn"


@pytest.mark.asyncio
async def test_response_translation_emits_tool_use_and_partial_json() -> None:
    async def source():
        yield 'data: {"type":"response.created","response":{"id":"resp_tool"}}\n\n'
        yield (
            'data: {"type":"response.output_item.added","item":'
            '{"type":"function_call","call_id":"call_1","name":"Read"}}\n\n'
        )
        yield 'data: {"type":"response.function_call_arguments.delta","delta":"{\\"path\\":\\"README.md\\"}"}\n\n'
        yield (
            'data: {"type":"response.output_item.done","item":'
            '{"type":"function_call","call_id":"call_1","name":"Read"}}\n\n'
        )
        yield 'data: {"type":"response.completed","response":{"usage":{"input_tokens":4,"output_tokens":2}}}\n\n'

    frames = [frame async for frame in responses_to_claude_sse(source())]
    payloads = [json.loads(frame.split("data: ", 1)[1]) for frame in frames]

    starts = [payload for payload in payloads if payload["type"] == "content_block_start"]
    deltas = [payload for payload in payloads if payload["type"] == "content_block_delta"]
    assert starts[0]["content_block"] == {"type": "tool_use", "id": "call_1", "name": "Read", "input": {}}
    assert deltas[0]["delta"] == {"type": "input_json_delta", "partial_json": '{"path":"README.md"}'}
    assert (
        next(payload for payload in payloads if payload["type"] == "message_delta")["delta"]["stop_reason"]
        == "tool_use"
    )


def test_local_token_count_does_not_depend_on_anthropic() -> None:
    assert estimate_claude_input_tokens({"model": "anything", "messages": []}) > 0
