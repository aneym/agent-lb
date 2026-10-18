from __future__ import annotations
import json
from tests.integration.test_http_responses_bridge import (
    _FakeBridgeUpstreamWebSocket,
    _FakeUpstreamMessage,
)


class _ClosingBridgeUpstreamWebSocket(_FakeBridgeUpstreamWebSocket):
    async def send_text(self, text: str) -> None:
        await super().send_text(text)
        await self._messages.put(_FakeUpstreamMessage("close", close_code=1000))


class _PrecreatedCloseUpstreamWebSocket(_FakeBridgeUpstreamWebSocket):
    async def send_text(self, text: str) -> None:
        self.sent_text.append(text)
        await self._messages.put(_FakeUpstreamMessage("close", close_code=1011))


class _CreatedOnlyUpstreamWebSocket(_FakeBridgeUpstreamWebSocket):
    async def send_text(self, text: str) -> None:
        self.sent_text.append(text)
        response_id = f"resp_created_only_{len(self.sent_text)}"
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


class _SilentUpstreamWebSocket(_FakeBridgeUpstreamWebSocket):
    async def send_text(self, text: str) -> None:
        self.sent_text.append(text)


class _RecordingUpstreamWebSocket(_FakeBridgeUpstreamWebSocket):
    pass


class _CreatedThenCloseUpstreamWebSocket(_FakeBridgeUpstreamWebSocket):
    async def send_text(self, text: str) -> None:
        self.sent_text.append(text)
        response_id = f"resp_created_then_close_{len(self.sent_text)}"
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
        await self._messages.put(_FakeUpstreamMessage("close", close_code=1011))


class _ProtocolOnlyTruncatedUpstreamWebSocket(_FakeBridgeUpstreamWebSocket):
    async def send_text(self, text: str) -> None:
        self.sent_text.append(text)
        response_id = f"resp_protocol_truncated_{len(self.sent_text)}"
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
                        "type": "response.in_progress",
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
                        "type": "response.failed",
                        "response": {
                            "id": response_id,
                            "object": "response",
                            "status": "failed",
                            "error": {
                                "type": "server_error",
                                "code": "upstream_stream_truncated",
                                "message": "Responses stream ended before a terminal event",
                            },
                        },
                    },
                    separators=(",", ":"),
                ),
            )
        )


class _TextThenTruncatedUpstreamWebSocket(_FakeBridgeUpstreamWebSocket):
    async def send_text(self, text: str) -> None:
        self.sent_text.append(text)
        response_id = f"resp_text_then_truncated_{len(self.sent_text)}"
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
                        "type": "response.output_text.delta",
                        "response": {"id": response_id},
                        "delta": "partial",
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
                            "id": response_id,
                            "object": "response",
                            "status": "failed",
                            "error": {
                                "type": "server_error",
                                "code": "upstream_stream_truncated",
                                "message": "Responses stream ended before a terminal event",
                            },
                        },
                    },
                    separators=(",", ":"),
                ),
            )
        )


class _ErrorOnlyUpstreamWebSocket(_FakeBridgeUpstreamWebSocket):
    async def send_text(self, text: str) -> None:
        self.sent_text.append(text)
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
                            "message": (
                                "The 'gpt-5.3-codex-spark' model is not supported when using Codex "
                                "with a ChatGPT account."
                            ),
                        },
                    },
                    separators=(",", ":"),
                ),
            )
        )


class _RateLimitErrorUpstreamWebSocket(_FakeBridgeUpstreamWebSocket):
    async def send_text(self, text: str) -> None:
        self.sent_text.append(text)
        await self._messages.put(
            _FakeUpstreamMessage(
                "text",
                text=json.dumps(
                    {
                        "type": "error",
                        "status": 429,
                        "error": {
                            "type": "rate_limit_error",
                            "code": "rate_limit_exceeded",
                            "message": "Rate limit reached for gpt-4o on tokens per day",
                            "plan_type": "team",
                            "resets_at": 1700000000,
                            "resets_in_seconds": 3600,
                        },
                    },
                    separators=(",", ":"),
                ),
            )
        )


class _FailingSendThenCloseUpstreamWebSocket(_FakeBridgeUpstreamWebSocket):
    async def send_text(self, text: str) -> None:
        self.sent_text.append(text)
        await self._messages.put(_FakeUpstreamMessage("close", close_code=1011))
        raise RuntimeError("socket closed during send")


class _PrewarmingBridgeUpstreamWebSocket(_FakeBridgeUpstreamWebSocket):
    async def send_text(self, text: str) -> None:
        self.sent_text.append(text)
        payload = json.loads(text)
        response_id = f"resp_prewarm_{len(self.sent_text)}"
        output = []
        usage = {
            "input_tokens": 12,
            "output_tokens": 0,
            "total_tokens": 12,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 0},
        }
        if payload.get("generate") is not False:
            response_id = f"resp_actual_{len(self.sent_text)}"
            output = [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "OK"}],
                }
            ]
            usage = {
                "input_tokens": 24,
                "output_tokens": 2,
                "total_tokens": 26,
                "input_tokens_details": {"cached_tokens": 20},
                "output_tokens_details": {"reasoning_tokens": 0},
            }
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
                            "output": output,
                            "usage": usage,
                        },
                    },
                    separators=(",", ":"),
                ),
            )
        )


class _TurnStateBridgeUpstreamWebSocket(_FakeBridgeUpstreamWebSocket):
    def __init__(self, turn_state: str) -> None:
        super().__init__()
        self._turn_state = turn_state

    def response_header(self, name: str) -> str | None:
        if name.lower() == "x-codex-turn-state":
            return self._turn_state
        return None


class _CompactionBridgeUpstreamWebSocket(_FakeBridgeUpstreamWebSocket):
    compaction_item = {
        "type": "compaction",
        "id": "cmp_bridge_1",
        "encrypted_content": "opaque-account-bound-blob",
    }

    async def send_text(self, text: str) -> None:
        self.sent_text.append(text)
        response_id = f"resp_compact_{len(self.sent_text)}"
        for event in (
            {
                "type": "response.created",
                "response": {"id": response_id, "object": "response", "status": "in_progress"},
            },
            {
                "type": "response.output_item.added",
                "output_index": 0,
                "item": {"type": "compaction", "id": "cmp_bridge_1"},
            },
            {
                "type": "response.output_item.done",
                "output_index": 0,
                "item": dict(self.compaction_item),
            },
            {
                "type": "response.completed",
                "response": {
                    "id": response_id,
                    "object": "response",
                    "status": "completed",
                    "output": [],
                    "usage": {
                        "input_tokens": 1000,
                        "output_tokens": 40,
                        "total_tokens": 1040,
                        "input_tokens_details": {"cached_tokens": 900},
                        "output_tokens_details": {"reasoning_tokens": 0},
                    },
                },
            },
        ):
            await self._messages.put(_FakeUpstreamMessage("text", text=json.dumps(event, separators=(",", ":"))))
