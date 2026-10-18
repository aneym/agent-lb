from __future__ import annotations
import asyncio
from collections import deque
from types import SimpleNamespace
import pytest
import app.modules.proxy.service as proxy_module


def _assert_codex_previous_response_stale_error(error: dict[str, object]) -> None:
    assert error["code"] == proxy_module.PREVIOUS_RESPONSE_STALE_CODE
    assert error["message"] == proxy_module.PREVIOUS_RESPONSE_STALE_MESSAGE


@pytest.fixture(autouse=True)
def _stub_request_logging(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_write_request_log(self, **kwargs):
        del self, kwargs
        return None

    monkeypatch.setattr(proxy_module.ProxyService, "_write_request_log", fake_write_request_log)


class _FakeUpstreamMessage:
    def __init__(
        self,
        kind: str,
        *,
        text: str | None = None,
        data: bytes | None = None,
        close_code: int | None = None,
        error: str | None = None,
    ) -> None:
        self.kind = kind
        self.text = text
        self.data = data
        self.close_code = close_code
        self.error = error


class _FakeUpstreamWebSocket:
    def __init__(self, messages: list[_FakeUpstreamMessage]) -> None:
        self.sent_text: list[str] = []
        self.sent_bytes: list[bytes] = []
        self.closed = False
        self._messages: asyncio.Queue[_FakeUpstreamMessage] = asyncio.Queue()
        for message in messages:
            self._messages.put_nowait(message)

    async def send_text(self, text: str) -> None:
        self.sent_text.append(text)

    async def send_bytes(self, data: bytes) -> None:
        self.sent_bytes.append(data)

    async def receive(self) -> _FakeUpstreamMessage:
        return await self._messages.get()

    async def close(self) -> None:
        self.closed = True


class _SequencedUpstreamWebSocket(_FakeUpstreamWebSocket):
    def __init__(
        self,
        messages: list[_FakeUpstreamMessage],
        *,
        deferred_message_batches: list[list[_FakeUpstreamMessage]] | None = None,
    ) -> None:
        super().__init__(messages)
        self._deferred_message_batches = deque(deferred_message_batches or [])

    async def send_text(self, text: str) -> None:
        await super().send_text(text)
        if not self._deferred_message_batches:
            return
        for message in self._deferred_message_batches.popleft():
            self._messages.put_nowait(message)


class _FailingSendUpstreamWebSocket(_FakeUpstreamWebSocket):
    async def send_text(self, text: str) -> None:
        await super().send_text(text)
        raise RuntimeError("socket closed during send")


class _DelayedUpstreamWebSocket(_FakeUpstreamWebSocket):
    def __init__(self, messages: list[_FakeUpstreamMessage], *, delays: list[float]) -> None:
        super().__init__(messages)
        self._delays = deque(delays)

    async def receive(self) -> _FakeUpstreamMessage:
        if self._delays:
            await asyncio.sleep(self._delays.popleft())
        return await super().receive()


def _websocket_settings(**overrides):
    values = {
        "prefer_earlier_reset_accounts": False,
        "sticky_threads_enabled": False,
        "openai_cache_affinity_max_age_seconds": 300,
        "openai_prompt_cache_key_derivation_enabled": True,
        "routing_strategy": "usage_weighted",
        "proxy_request_budget_seconds": 75.0,
        "stream_idle_timeout_seconds": 300.0,
        "proxy_downstream_websocket_idle_timeout_seconds": 120.0,
        "http_responses_session_bridge_instance_id": "test-instance",
        "sse_keepalive_interval_seconds": 10.0,
        "log_proxy_request_shape": False,
        "log_proxy_request_shape_raw_cache_key": False,
        "proxy_token_refresh_limit": 32,
        "proxy_upstream_websocket_connect_limit": 64,
        "proxy_response_create_limit": 64,
        "proxy_compact_response_create_limit": 16,
    }
    values.update(overrides)
    return SimpleNamespace(**values)
