from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.integration


def _extract_first_event(lines: list[str]) -> dict:
    for line in lines:
        if line.startswith("data: ") and not line.startswith("data: [DONE]"):
            return json.loads(line[6:])
    raise AssertionError("No SSE data event found")


@pytest.mark.asyncio
async def test_codex_style_responses_payload_is_accepted(async_client):
    payload = {
        "model": "gpt-5.1",
        "instructions": "You are Codex.",
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "hi"},
                ],
            }
        ],
        "tool_choice": "auto",
        "parallel_tool_calls": False,
        "stream": True,
        "include": ["message.output_text.logprobs"],
    }
    async with async_client.stream("POST", "/v1/responses", json=payload) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]





    assert not lines[0].startswith(":"), lines[:3]







    event = _extract_first_event(lines)
    assert event["type"] == "response.created"


    terminal_types = {"response.completed", "response.incomplete", "response.failed", "error"}
    terminal_events = [
        json.loads(line[6:])
        for line in lines
        if line.startswith("data: ")
        and not line.startswith("data: [DONE]")
        and json.loads(line[6:]).get("type") in terminal_types
    ]
    assert terminal_events, "Expected at least one terminal event in the stream"
