from __future__ import annotations

import json
from typing import Any


def sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def codex_rate_limits_event() -> str:
    return sse(
        {
            "type": "codex.rate_limits",
            "plan_type": "pro",
            "rate_limits": {"allowed": True, "limit_reached": False},
        }
    )


def response_created(resp_id: str, seq: int = 0) -> str:
    return sse(
        {
            "type": "response.created",
            "sequence_number": seq,
            "response": {"id": resp_id, "object": "response", "status": "in_progress", "output": []},
        }
    )


def response_completed_empty(resp_id: str, seq: int, *, usage: dict | None = None) -> str:
    body: dict[str, Any] = {
        "id": resp_id,
        "object": "response",
        "status": "completed",
        "output": [],
    }
    body["usage"] = usage or {
        "input_tokens": 4,
        "output_tokens": 7,
        "total_tokens": 11,
    }
    return sse(
        {
            "type": "response.completed",
            "sequence_number": seq,
            "response": body,
        }
    )


def message_output_block(item_id: str, text: str, output_index: int, start_seq: int) -> list[str]:
    return [
        sse(
            {
                "type": "response.output_item.added",
                "sequence_number": start_seq,
                "output_index": output_index,
                "item": {"id": item_id, "type": "message", "role": "assistant", "status": "in_progress", "content": []},
            }
        ),
        sse(
            {
                "type": "response.content_part.added",
                "sequence_number": start_seq + 1,
                "output_index": output_index,
                "content_index": 0,
                "item_id": item_id,
                "part": {"type": "output_text", "text": ""},
            }
        ),
        sse(
            {
                "type": "response.output_text.delta",
                "sequence_number": start_seq + 2,
                "output_index": output_index,
                "content_index": 0,
                "item_id": item_id,
                "delta": text,
                "logprobs": [],
            }
        ),
        sse(
            {
                "type": "response.output_text.done",
                "sequence_number": start_seq + 3,
                "output_index": output_index,
                "content_index": 0,
                "item_id": item_id,
                "text": text,
                "logprobs": [],
            }
        ),
        sse(
            {
                "type": "response.content_part.done",
                "sequence_number": start_seq + 4,
                "output_index": output_index,
                "content_index": 0,
                "item_id": item_id,
                "part": {"type": "output_text", "text": text},
            }
        ),
        sse(
            {
                "type": "response.output_item.done",
                "sequence_number": start_seq + 5,
                "output_index": output_index,
                "item": {
                    "id": item_id,
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": text}],
                },
            }
        ),
    ]


def function_call_output_block(call_id: str, name: str, args: str, output_index: int, start_seq: int) -> list[str]:
    fc_id = f"fc_{call_id}"
    return [
        sse(
            {
                "type": "response.output_item.added",
                "sequence_number": start_seq,
                "output_index": output_index,
                "item": {
                    "id": fc_id,
                    "type": "function_call",
                    "status": "in_progress",
                    "call_id": call_id,
                    "name": name,
                    "arguments": "",
                },
            }
        ),
        sse(
            {
                "type": "response.function_call_arguments.delta",
                "sequence_number": start_seq + 1,
                "output_index": output_index,
                "item_id": fc_id,
                "delta": args,
            }
        ),
        sse(
            {
                "type": "response.function_call_arguments.done",
                "sequence_number": start_seq + 2,
                "output_index": output_index,
                "item_id": fc_id,
                "arguments": args,
            }
        ),
        sse(
            {
                "type": "response.output_item.done",
                "sequence_number": start_seq + 3,
                "output_index": output_index,
                "item": {
                    "id": fc_id,
                    "type": "function_call",
                    "status": "completed",
                    "call_id": call_id,
                    "name": name,
                    "arguments": args,
                },
            }
        ),
    ]
