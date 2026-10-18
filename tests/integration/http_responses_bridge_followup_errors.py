from __future__ import annotations
import asyncio
import json
from tests.integration.test_http_responses_bridge import (
    _FakeBridgeUpstreamWebSocket,
    _FakeUpstreamMessage,
)


class _TwoFollowupsPreviousResponseNotFoundUpstreamWebSocket(_FakeBridgeUpstreamWebSocket):
    def __init__(self) -> None:
        super().__init__()
        self.first_followup_created = asyncio.Event()

    async def send_text(self, text: str) -> None:
        self.sent_text.append(text)
        if len(self.sent_text) == 1:
            await self._messages.put(
                _FakeUpstreamMessage(
                    "text",
                    text=json.dumps(
                        {
                            "type": "response.created",
                            "response": {
                                "id": "resp_bridge_prev_anchor_a",
                                "object": "response",
                                "status": "in_progress",
                            },
                        },
                        separators=(",", ":"),
                    ),
                )
            )
            await self._messages.put(
                _FakeUpstreamMessage(
                    "text",
                    text=json.dumps(
                        {
                            "type": "response.completed",
                            "response": {
                                "id": "resp_bridge_prev_anchor_a",
                                "object": "response",
                                "status": "completed",
                                "output": [
                                    {
                                        "type": "message",
                                        "role": "assistant",
                                        "content": [{"type": "output_text", "text": "OK"}],
                                    }
                                ],
                                "usage": {
                                    "input_tokens": 24,
                                    "output_tokens": 2,
                                    "total_tokens": 26,
                                    "input_tokens_details": {"cached_tokens": 20},
                                    "output_tokens_details": {"reasoning_tokens": 0},
                                },
                            },
                        },
                        separators=(",", ":"),
                    ),
                )
            )
            return

        if len(self.sent_text) == 2:
            await self._messages.put(
                _FakeUpstreamMessage(
                    "text",
                    text=json.dumps(
                        {
                            "type": "response.created",
                            "response": {
                                "id": "resp_bridge_prev_anchor_b",
                                "object": "response",
                                "status": "in_progress",
                            },
                        },
                        separators=(",", ":"),
                    ),
                )
            )
            await self._messages.put(
                _FakeUpstreamMessage(
                    "text",
                    text=json.dumps(
                        {
                            "type": "response.completed",
                            "response": {
                                "id": "resp_bridge_prev_anchor_b",
                                "object": "response",
                                "status": "completed",
                                "output": [
                                    {
                                        "type": "message",
                                        "role": "assistant",
                                        "content": [{"type": "output_text", "text": "OK"}],
                                    }
                                ],
                                "usage": {
                                    "input_tokens": 24,
                                    "output_tokens": 2,
                                    "total_tokens": 26,
                                    "input_tokens_details": {"cached_tokens": 20},
                                    "output_tokens_details": {"reasoning_tokens": 0},
                                },
                            },
                        },
                        separators=(",", ":"),
                    ),
                )
            )
            return

        if len(self.sent_text) == 3:
            await self._messages.put(
                _FakeUpstreamMessage(
                    "text",
                    text=json.dumps(
                        {
                            "type": "response.created",
                            "response": {
                                "id": "resp_bridge_followup_a",
                                "object": "response",
                                "status": "in_progress",
                            },
                        },
                        separators=(",", ":"),
                    ),
                )
            )
            self.first_followup_created.set()
            return

        if len(self.sent_text) == 4:
            await self._messages.put(
                _FakeUpstreamMessage(
                    "text",
                    text=json.dumps(
                        {
                            "type": "response.created",
                            "response": {
                                "id": "resp_bridge_followup_b",
                                "object": "response",
                                "status": "in_progress",
                            },
                        },
                        separators=(",", ":"),
                    ),
                )
            )
            await self._messages.put(
                _FakeUpstreamMessage(
                    "text",
                    text=json.dumps(
                        {
                            "type": "error",
                            "status": 400,
                            "error": {
                                "type": "invalid_request_error",
                                "code": "previous_response_not_found",
                                "message": (
                                    "Cannot continue conversation because upstream lost resp_bridge_prev_anchor_a."
                                ),
                                "param": "previous_response_id",
                            },
                        },
                        separators=(",", ":"),
                    ),
                )
            )
            await self._messages.put(
                _FakeUpstreamMessage(
                    "text",
                    text=json.dumps(
                        {
                            "type": "response.completed",
                            "response": {
                                "id": "resp_bridge_followup_b",
                                "object": "response",
                                "status": "completed",
                                "output": [
                                    {
                                        "type": "message",
                                        "role": "assistant",
                                        "content": [{"type": "output_text", "text": "OK"}],
                                    }
                                ],
                                "usage": {
                                    "input_tokens": 24,
                                    "output_tokens": 2,
                                    "total_tokens": 26,
                                    "input_tokens_details": {"cached_tokens": 20},
                                    "output_tokens_details": {"reasoning_tokens": 0},
                                },
                            },
                        },
                        separators=(",", ":"),
                    ),
                )
            )
            return

        response_id = "resp_bridge_after_error"
        await self._messages.put(
            _FakeUpstreamMessage(
                "text",
                text=json.dumps(
                    {
                        "type": "response.created",
                        "response": {"id": response_id, "object": "response", "status": "in_progress"},
                    },
                    separators=(",", ":"),
                ),
            )
        )
        await self._messages.put(
            _FakeUpstreamMessage(
                "text",
                text=json.dumps(
                    {
                        "type": "response.completed",
                        "response": {
                            "id": response_id,
                            "object": "response",
                            "status": "completed",
                            "output": [
                                {
                                    "type": "message",
                                    "role": "assistant",
                                    "content": [{"type": "output_text", "text": "OK"}],
                                }
                            ],
                            "usage": {
                                "input_tokens": 24,
                                "output_tokens": 2,
                                "total_tokens": 26,
                                "input_tokens_details": {"cached_tokens": 20},
                                "output_tokens_details": {"reasoning_tokens": 0},
                            },
                        },
                    },
                    separators=(",", ":"),
                ),
            )
        )


class _TwoSameAnchorFollowupsPreviousResponseNotFoundUpstreamWebSocket(_FakeBridgeUpstreamWebSocket):
    def __init__(self) -> None:
        super().__init__()
        self.first_followup_created = asyncio.Event()

    async def send_text(self, text: str) -> None:
        self.sent_text.append(text)
        if len(self.sent_text) == 1:
            await self._messages.put(
                _FakeUpstreamMessage(
                    "text",
                    text=json.dumps(
                        {
                            "type": "response.created",
                            "response": {
                                "id": "resp_bridge_prev_anchor_shared",
                                "object": "response",
                                "status": "in_progress",
                            },
                        },
                        separators=(",", ":"),
                    ),
                )
            )
            await self._messages.put(
                _FakeUpstreamMessage(
                    "text",
                    text=json.dumps(
                        {
                            "type": "response.completed",
                            "response": {
                                "id": "resp_bridge_prev_anchor_shared",
                                "object": "response",
                                "status": "completed",
                                "output": [
                                    {
                                        "type": "message",
                                        "role": "assistant",
                                        "content": [{"type": "output_text", "text": "OK"}],
                                    }
                                ],
                                "usage": {
                                    "input_tokens": 24,
                                    "output_tokens": 2,
                                    "total_tokens": 26,
                                    "input_tokens_details": {"cached_tokens": 20},
                                    "output_tokens_details": {"reasoning_tokens": 0},
                                },
                            },
                        },
                        separators=(",", ":"),
                    ),
                )
            )
            return

        if len(self.sent_text) == 2:
            await self._messages.put(
                _FakeUpstreamMessage(
                    "text",
                    text=json.dumps(
                        {
                            "type": "response.created",
                            "response": {
                                "id": "resp_bridge_followup_same_anchor_a",
                                "object": "response",
                                "status": "in_progress",
                            },
                        },
                        separators=(",", ":"),
                    ),
                )
            )
            self.first_followup_created.set()
            return

        if len(self.sent_text) == 3:
            await self._messages.put(
                _FakeUpstreamMessage(
                    "text",
                    text=json.dumps(
                        {
                            "type": "response.created",
                            "response": {
                                "id": "resp_bridge_followup_same_anchor_b",
                                "object": "response",
                                "status": "in_progress",
                            },
                        },
                        separators=(",", ":"),
                    ),
                )
            )
            await self._messages.put(
                _FakeUpstreamMessage(
                    "text",
                    text=json.dumps(
                        {
                            "type": "error",
                            "status": 400,
                            "error": {
                                "type": "invalid_request_error",
                                "code": "previous_response_not_found",
                                "message": "Previous response with id 'resp_bridge_prev_anchor_shared' not found.",
                                "param": "previous_response_id",
                            },
                        },
                        separators=(",", ":"),
                    ),
                )
            )
            return

        response_id = "resp_bridge_after_same_anchor_error"
        await self._messages.put(
            _FakeUpstreamMessage(
                "text",
                text=json.dumps(
                    {
                        "type": "response.created",
                        "response": {"id": response_id, "object": "response", "status": "in_progress"},
                    },
                    separators=(",", ":"),
                ),
            )
        )
        await self._messages.put(
            _FakeUpstreamMessage(
                "text",
                text=json.dumps(
                    {
                        "type": "response.completed",
                        "response": {
                            "id": response_id,
                            "object": "response",
                            "status": "completed",
                            "output": [
                                {
                                    "type": "message",
                                    "role": "assistant",
                                    "content": [{"type": "output_text", "text": "OK"}],
                                }
                            ],
                            "usage": {
                                "input_tokens": 24,
                                "output_tokens": 2,
                                "total_tokens": 26,
                                "input_tokens_details": {"cached_tokens": 20},
                                "output_tokens_details": {"reasoning_tokens": 0},
                            },
                        },
                    },
                    separators=(",", ":"),
                ),
            )
        )
