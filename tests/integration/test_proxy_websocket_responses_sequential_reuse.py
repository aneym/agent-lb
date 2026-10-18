from __future__ import annotations
import asyncio
import json
from threading import Event
from types import SimpleNamespace
from typing import cast
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
import app.modules.proxy.api as proxy_api_module
import app.modules.proxy.service as proxy_module
from app.db.models import RequestLog
from app.db.session import SessionLocal
from tests.integration.proxy_websocket_responses_support import (
    _FakeUpstreamMessage,
    _SequencedUpstreamWebSocket,
    _stub_request_logging,
    _websocket_settings,
)

pytestmark = pytest.mark.integration


def test_v1_responses_websocket_reuses_upstream_for_sequential_requests(app_instance, monkeypatch):
    class _DeferredSecondTurnUpstream(_SequencedUpstreamWebSocket):
        async def send_text(self, text: str) -> None:
            self.sent_text.append(text)
            if self._deferred_message_batches:
                batch = self._deferred_message_batches.popleft()
                loop = asyncio.get_running_loop()
                loop.call_later(0.05, lambda: [self._messages.put_nowait(message) for message in batch])

    first_upstream = _DeferredSecondTurnUpstream(
        [
            _FakeUpstreamMessage(
                "text",
                text=json.dumps(
                    {"type": "response.created", "response": {"id": "resp_ws_first", "status": "in_progress"}},
                    separators=(",", ":"),
                ),
            ),
            _FakeUpstreamMessage(
                "text",
                text=json.dumps(
                    {
                        "type": "response.completed",
                        "response": {
                            "id": "resp_ws_first",
                            "status": "completed",
                            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                        },
                    },
                    separators=(",", ":"),
                ),
            ),
        ],
        deferred_message_batches=[
            [
                _FakeUpstreamMessage(
                    "text",
                    text=json.dumps(
                        {"type": "response.created", "response": {"id": "resp_ws_second", "status": "in_progress"}},
                        separators=(",", ":"),
                    ),
                ),
                _FakeUpstreamMessage(
                    "text",
                    text=json.dumps(
                        {
                            "type": "response.completed",
                            "response": {
                                "id": "resp_ws_second",
                                "status": "completed",
                                "usage": {"input_tokens": 2, "output_tokens": 1, "total_tokens": 3},
                            },
                        },
                        separators=(",", ":"),
                    ),
                ),
            ]
        ],
    )
    connect_calls: list[dict[str, object]] = []

    async def add_test_account():
        async with SessionLocal() as session:
            session.add(
                proxy_module.Account(
                    id="acct_ws_proxy_1",
                    chatgpt_account_id="acct_ws_proxy_1",
                    email="invented@example.invalid",
                    plan_type="plus",
                    access_token_encrypted=b"access",
                    refresh_token_encrypted=b"refresh",
                    last_refresh=proxy_module.utcnow(),
                    status=proxy_module.AccountStatus.ACTIVE,
                )
            )
            await session.commit()

    asyncio.run(add_test_account())

    class _FakeSettingsCache:
        async def get(self):
            return _websocket_settings()

    async def allow_firewall(_websocket):
        return None

    async def allow_proxy_api_key(_authorization: str | None, *, request: object | None = None):
        return None

    async def fake_connect_proxy_websocket(
        self,
        headers,
        *,
        sticky_key,
        sticky_kind,
        reallocate_sticky,
        sticky_max_age_seconds,
        prefer_earlier_reset,
        prefer_earlier_reset_window,
        routing_strategy,
        model,
        request_state,
        api_key,
        client_send_lock,
        websocket,
    ):
        del self, headers, request_state, api_key, client_send_lock, websocket
        connect_calls.append(
            {
                "sticky_key": sticky_key,
                "sticky_kind": sticky_kind,
                "reallocate_sticky": reallocate_sticky,
                "sticky_max_age_seconds": sticky_max_age_seconds,
                "prefer_earlier_reset": prefer_earlier_reset,
                "routing_strategy": routing_strategy,
                "model": model,
            }
        )
        return SimpleNamespace(id=f"acct_ws_proxy_{len(connect_calls)}"), first_upstream

    first_logged = Event()
    second_logged = Event()

    async def write_request_log(self, **kwargs):
        await proxy_module._RequestLogMixin._write_request_log(self, **kwargs)
        if not first_logged.is_set():
            first_logged.set()
        else:
            second_logged.set()

    async def persisted_turns():
        async with SessionLocal() as session:
            result = await session.execute(
                select(RequestLog).where(RequestLog.request_id.in_(("resp_ws_first", "resp_ws_second")))
            )
            return {
                row.request_id: (row.caller_user, row.caller_user_source, row.caller_machine, row.caller_machine_source)
                for row in result.scalars()
            }

    monkeypatch.setattr(proxy_api_module, "_websocket_firewall_denial_response", allow_firewall)
    monkeypatch.setattr(proxy_api_module, "validate_proxy_api_key_authorization", allow_proxy_api_key)
    monkeypatch.setattr(proxy_module, "get_settings_cache", lambda: _FakeSettingsCache())
    monkeypatch.setattr(proxy_module.ProxyService, "_connect_proxy_websocket", fake_connect_proxy_websocket)
    monkeypatch.setattr(proxy_module.ProxyService, "_write_request_log", write_request_log)

    first_request = {
        "type": "response.create",
        "model": "gpt-5.4",
        "input": "first",
        "promptCacheKey": "thread_a",
        "stream": True,
    }
    second_request = {
        "type": "response.create",
        "model": "gpt-5.5",
        "input": "second",
        "promptCacheKey": "thread_b",
        "stream": True,
    }

    with TestClient(app_instance) as client:
        with client.websocket_connect("/v1/responses") as websocket:
            raw_extra_headers = cast(list[tuple[bytes, bytes]], websocket.extra_headers)
            extra_headers = {key.decode(): value.decode() for key, value in raw_extra_headers}
            turn_state = extra_headers["x-codex-turn-state"]
            websocket.send_text(json.dumps(first_request))
            first_events = [json.loads(websocket.receive_text()) for _ in range(2)]
            assert first_logged.wait(timeout=5), "first websocket turn log was not persisted"

            websocket.send_text(json.dumps(second_request))
            second_events = [json.loads(websocket.receive_text()) for _ in range(2)]

            assert second_logged.wait(timeout=5), "second websocket turn log was not persisted"

    assert [event["type"] for event in first_events] == ["response.created", "response.completed"]
    assert [event["type"] for event in second_events] == ["response.created", "response.completed"]

    assert asyncio.run(persisted_turns()) == {
        "resp_ws_first": ("unknown", "unknown", "remote", "remote"),
        "resp_ws_second": ("unknown", "unknown", "remote", "remote"),
    }
    assert len(connect_calls) == 1
    assert connect_calls[0]["sticky_key"] == turn_state
    assert connect_calls[0]["sticky_kind"] == proxy_module.StickySessionKind.CODEX_SESSION
    assert connect_calls[0]["model"] == "gpt-5.4"
    assert [json.loads(message) for message in first_upstream.sent_text] == [
        {
            "model": "gpt-5.4",
            "instructions": "",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "first"}]}],
            "tools": [],
            "store": False,
            "include": [],
            "prompt_cache_key": "thread_a",
            "type": "response.create",
        },
        {
            "model": "gpt-5.5",
            "instructions": "",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "second"}]}],
            "tools": [],
            "store": False,
            "include": [],
            "prompt_cache_key": "thread_b",
            "type": "response.create",
        },
    ]
