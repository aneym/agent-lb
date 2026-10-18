from __future__ import annotations
import asyncio
import json
from tests.integration.test_http_responses_bridge import (
    _FakeBridgeUpstreamWebSocket,
    _FakeUpstreamMessage,
)


class _PreviousResponseNotFoundUpstreamWebSocket(_FakeBridgeUpstreamWebSocket):
    async def send_text(self, text: str) -> None:
        self.sent_text.append(text)
        payload = json.loads(text)
        previous_response_id = payload.get("previous_response_id")
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
                            "message": f"Previous response with id '{previous_response_id}' not found.",
                            "param": "previous_response_id",
                        },
                    },
                    separators=(",", ":"),
                ),
            )
        )


class _AnonymousPreviousResponseNotFoundWithInflightUpstreamWebSocket(_FakeBridgeUpstreamWebSocket):
    def __init__(self) -> None:
        super().__init__()
        self.first_request_created = asyncio.Event()

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
                                "id": "resp_bridge_inflight",
                                "object": "response",
                                "status": "in_progress",
                            },
                        },
                        separators=(",", ":"),
                    ),
                )
            )
            self.first_request_created.set()
            return

        payload = json.loads(text)
        previous_response_id = payload.get("previous_response_id")
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
                            "message": f"Previous response with id '{previous_response_id}' not found.",
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
                            "id": "resp_bridge_inflight",
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


class _InvalidRequestPreviousResponseUpstreamWebSocket(_FakeBridgeUpstreamWebSocket):
    async def send_text(self, text: str) -> None:
        self.sent_text.append(text)
        payload = json.loads(text)
        previous_response_id = payload.get("previous_response_id")
        await self._messages.put(
            _FakeUpstreamMessage(
                "text",
                text=json.dumps(
                    {
                        "type": "error",
                        "status": 400,
                        "error": {
                            "type": "invalid_request_error",
                            "code": "invalid_request_error",
                            "message": f"Previous response with id '{previous_response_id}' not found.",
                            "param": "previous_response_id",
                        },
                    },
                    separators=(",", ":"),
                ),
            )
        )


class _ForeignPreviousResponseNotFoundAfterCreatedUpstreamWebSocket(_FakeBridgeUpstreamWebSocket):
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
                                "id": "resp_bridge_prev_anchor",
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
                                "id": "resp_bridge_prev_anchor",
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
            payload = json.loads(text)
            previous_response_id = payload.get("previous_response_id")
            await self._messages.put(
                _FakeUpstreamMessage(
                    "text",
                    text=json.dumps(
                        {
                            "type": "response.created",
                            "response": {
                                "id": "resp_bridge_followup_created",
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
                            "type": "response.failed",
                            "response": {
                                "id": "resp_bridge_foreign_prev_nf",
                                "object": "response",
                                "status": "failed",
                                "error": {
                                    "type": "invalid_request_error",
                                    "code": "previous_response_not_found",
                                    "message": f"Previous response with id '{previous_response_id}' not found.",
                                    "param": "previous_response_id",
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


class _AnonymousPreviousResponseNotFoundAfterCreatedUpstreamWebSocket(_FakeBridgeUpstreamWebSocket):
    def __init__(self) -> None:
        super().__init__()
        self.first_request_created = asyncio.Event()

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
                                "id": "resp_bridge_inflight",
                                "object": "response",
                                "status": "in_progress",
                            },
                        },
                        separators=(",", ":"),
                    ),
                )
            )
            self.first_request_created.set()
            return

        if len(self.sent_text) == 2:
            payload = json.loads(text)
            previous_response_id = payload.get("previous_response_id")
            await self._messages.put(
                _FakeUpstreamMessage(
                    "text",
                    text=json.dumps(
                        {
                            "type": "response.created",
                            "response": {
                                "id": "resp_bridge_followup_created",
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
                                "message": f"Previous response with id '{previous_response_id}' not found.",
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
                                "id": "resp_bridge_inflight",
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
