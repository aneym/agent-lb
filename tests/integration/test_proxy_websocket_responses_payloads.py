from __future__ import annotations
import json
from types import SimpleNamespace
from typing import cast
import pytest
from fastapi.testclient import TestClient
import app.modules.proxy.api as proxy_api_module
import app.modules.proxy.service as proxy_module
from tests.integration.proxy_websocket_responses_support import (
    _FakeUpstreamMessage,
    _FakeUpstreamWebSocket,
    _stub_request_logging,
    _websocket_settings,
)

pytestmark = pytest.mark.integration


def test_v1_responses_websocket_normalizes_payload_before_forwarding(app_instance, monkeypatch):
    upstream_messages = [
        _FakeUpstreamMessage(
            "text",
            text=json.dumps(
                {"type": "response.created", "response": {"id": "resp_ws_v1", "status": "in_progress"}},
                separators=(",", ":"),
            ),
        ),
        _FakeUpstreamMessage(
            "text",
            text=json.dumps(
                {
                    "type": "response.completed",
                    "response": {
                        "id": "resp_ws_v1",
                        "status": "completed",
                        "usage": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
                    },
                },
                separators=(",", ":"),
            ),
        ),
    ]
    fake_upstream = _FakeUpstreamWebSocket(upstream_messages)
    seen: dict[str, object] = {}

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
        seen["sticky_key"] = sticky_key
        seen["sticky_kind"] = sticky_kind
        seen["reallocate_sticky"] = reallocate_sticky
        seen["sticky_max_age_seconds"] = sticky_max_age_seconds
        seen["prefer_earlier_reset"] = prefer_earlier_reset
        seen["prefer_earlier_reset_window"] = prefer_earlier_reset_window
        seen["routing_strategy"] = routing_strategy
        seen["model"] = model
        return SimpleNamespace(id="acct_ws_proxy_v1"), fake_upstream

    async def fake_write_request_log(self, **kwargs):
        del self, kwargs

    monkeypatch.setattr(proxy_api_module, "_websocket_firewall_denial_response", allow_firewall)
    monkeypatch.setattr(proxy_api_module, "validate_proxy_api_key_authorization", allow_proxy_api_key)
    monkeypatch.setattr(proxy_module, "get_settings_cache", lambda: _FakeSettingsCache())
    monkeypatch.setattr(proxy_module.ProxyService, "_connect_proxy_websocket", fake_connect_proxy_websocket)
    monkeypatch.setattr(proxy_module.ProxyService, "_write_request_log", fake_write_request_log)

    request_payload = {
        "type": "response.create",
        "model": "gpt-5.4",
        "input": "cache me",
        "promptCacheKey": "thread_alias",
        "promptCacheRetention": "12h",
        "tools": [{"type": "web_search_preview"}],
        "service_tier": "priority",
        "stream": True,
    }

    with TestClient(app_instance) as client:
        with client.websocket_connect("/v1/responses") as websocket:
            raw_extra_headers = cast(list[tuple[bytes, bytes]], websocket.extra_headers)
            extra_headers = {key.decode(): value.decode() for key, value in raw_extra_headers}
            turn_state = extra_headers["x-codex-turn-state"]
            websocket.send_text(json.dumps(request_payload))
            first = json.loads(websocket.receive_text())
            second = json.loads(websocket.receive_text())

    assert first["type"] == "response.created"
    assert second["type"] == "response.completed"
    assert seen["sticky_key"] == turn_state
    assert seen["sticky_kind"] == proxy_module.StickySessionKind.CODEX_SESSION
    assert seen["reallocate_sticky"] is False
    assert seen["sticky_max_age_seconds"] is None
    assert seen["prefer_earlier_reset_window"] == "secondary"
    assert seen["model"] == "gpt-5.4"
    assert [json.loads(message) for message in fake_upstream.sent_text] == [
        {
            "model": "gpt-5.4",
            "instructions": "",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "cache me"}]}],
            "tools": [{"type": "web_search"}],
            "service_tier": "priority",
            "store": False,
            "include": [],
            "prompt_cache_key": "thread_alias",
            "type": "response.create",
        }
    ]


def test_v1_responses_websocket_rejects_invalid_payload_before_connect(app_instance, monkeypatch):
    called = {"connect": False}

    class _FakeSettingsCache:
        async def get(self):
            return _websocket_settings()

    async def allow_firewall(_websocket):
        return None

    async def allow_proxy_api_key(_authorization: str | None, *, request: object | None = None):
        return None

    async def fail_connect_proxy_websocket(*args, **kwargs):
        del args, kwargs
        called["connect"] = True
        raise AssertionError("invalid websocket payload must not open upstream")

    monkeypatch.setattr(proxy_api_module, "_websocket_firewall_denial_response", allow_firewall)
    monkeypatch.setattr(proxy_api_module, "validate_proxy_api_key_authorization", allow_proxy_api_key)
    monkeypatch.setattr(proxy_module, "get_settings_cache", lambda: _FakeSettingsCache())
    monkeypatch.setattr(proxy_module.ProxyService, "_connect_proxy_websocket", fail_connect_proxy_websocket)

    with TestClient(app_instance) as client:
        with client.websocket_connect("/v1/responses") as websocket:
            websocket.send_text(
                json.dumps(
                    {
                        "type": "response.create",
                        "model": "gpt-5.4",
                        "input": "hi",
                        "truncation": "middle",
                    }
                )
            )
            json.loads(websocket.receive_text())

    assert called["connect"] is False


def test_backend_responses_websocket_forwards_previous_response_id(app_instance, monkeypatch):
    fake_upstream = _FakeUpstreamWebSocket(
        [
            _FakeUpstreamMessage(
                "text",
                text=json.dumps(
                    {"type": "response.created", "response": {"id": "resp_ws_prev", "status": "in_progress"}},
                    separators=(",", ":"),
                ),
            ),
            _FakeUpstreamMessage(
                "text",
                text=json.dumps(
                    {"type": "response.completed", "response": {"id": "resp_ws_prev", "status": "completed"}},
                    separators=(",", ":"),
                ),
            ),
        ]
    )
    seen: dict[str, object] = {}

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
        prefer_earlier_reset,
        prefer_earlier_reset_window,
        routing_strategy,
        model,
        request_state,
        api_key,
        client_send_lock,
        websocket,
        reallocate_sticky=False,
        sticky_max_age_seconds=None,
    ):
        del self, sticky_key, sticky_kind, prefer_earlier_reset, routing_strategy, model
        del request_state, api_key, client_send_lock, websocket, reallocate_sticky, sticky_max_age_seconds
        seen["headers"] = dict(headers)
        return SimpleNamespace(id="acct_ws_prev"), fake_upstream

    async def fake_write_request_log(self, **kwargs):
        del self, kwargs

    monkeypatch.setattr(proxy_api_module, "_websocket_firewall_denial_response", allow_firewall)
    monkeypatch.setattr(proxy_api_module, "validate_proxy_api_key_authorization", allow_proxy_api_key)
    monkeypatch.setattr(proxy_module, "get_settings_cache", lambda: _FakeSettingsCache())
    monkeypatch.setattr(proxy_module.ProxyService, "_connect_proxy_websocket", fake_connect_proxy_websocket)
    monkeypatch.setattr(proxy_module.ProxyService, "_write_request_log", fake_write_request_log)

    request_payload = {
        "type": "response.create",
        "model": "gpt-5.4",
        "instructions": "",
        "previous_response_id": "resp_prev_123",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "continue"}]}],
        "stream": True,
    }

    with TestClient(app_instance) as client:
        with client.websocket_connect(
            "/backend-api/codex/responses",
            headers={
                "Authorization": "Bearer external-token",
                "session_id": "thread-ws-prev-1",
                "openai-beta": "responses_websockets=2026-02-06",
            },
        ) as websocket:
            websocket.send_text(json.dumps(request_payload))
            first = json.loads(websocket.receive_text())
            second = json.loads(websocket.receive_text())

    assert first["type"] == "response.created"
    assert second["type"] == "response.completed"
    assert [json.loads(message) for message in fake_upstream.sent_text] == [
        {
            "model": "gpt-5.4",
            "instructions": "",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "continue"}]}],
            "tools": [],
            "store": False,
            "include": [],
            "previous_response_id": "resp_prev_123",
            "type": "response.create",
        }
    ]


def test_backend_responses_websocket_trims_replayed_tool_call_items_with_previous_response_id(
    app_instance,
    monkeypatch,
):
    fake_upstream = _FakeUpstreamWebSocket(
        [
            _FakeUpstreamMessage(
                "text",
                text=json.dumps(
                    {"type": "response.created", "response": {"id": "resp_ws_tool_output", "status": "in_progress"}},
                    separators=(",", ":"),
                ),
            ),
            _FakeUpstreamMessage(
                "text",
                text=json.dumps(
                    {"type": "response.completed", "response": {"id": "resp_ws_tool_output", "status": "completed"}},
                    separators=(",", ":"),
                ),
            ),
        ]
    )

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
        prefer_earlier_reset,
        prefer_earlier_reset_window,
        routing_strategy,
        model,
        request_state,
        api_key,
        client_send_lock,
        websocket,
        reallocate_sticky=False,
        sticky_max_age_seconds=None,
    ):
        del self, headers, sticky_key, sticky_kind, prefer_earlier_reset, routing_strategy, model
        del request_state, api_key, client_send_lock, websocket, reallocate_sticky, sticky_max_age_seconds
        return SimpleNamespace(id="acct_ws_tool_output"), fake_upstream

    async def fake_write_request_log(self, **kwargs):
        del self, kwargs

    monkeypatch.setattr(proxy_api_module, "_websocket_firewall_denial_response", allow_firewall)
    monkeypatch.setattr(proxy_api_module, "validate_proxy_api_key_authorization", allow_proxy_api_key)
    monkeypatch.setattr(proxy_module, "get_settings_cache", lambda: _FakeSettingsCache())
    monkeypatch.setattr(proxy_module.ProxyService, "_connect_proxy_websocket", fake_connect_proxy_websocket)
    monkeypatch.setattr(proxy_module.ProxyService, "_write_request_log", fake_write_request_log)

    request_payload = {
        "type": "response.create",
        "model": "gpt-5.4",
        "instructions": "",
        "previous_response_id": "resp_prev_tool_call",
        "input": [
            {"type": "reasoning", "summary": []},
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "running command"}],
            },
            {
                "type": "function_call",
                "call_id": "call_repeat",
                "name": "exec_command",
                "arguments": '{"cmd":"date"}',
            },
            {
                "type": "function_call_output",
                "call_id": "call_repeat",
                "output": "Wed May 6 16:00:00 UTC 2026",
            },
        ],
        "stream": True,
    }

    with TestClient(app_instance) as client:
        with client.websocket_connect("/backend-api/codex/responses") as websocket:
            websocket.send_text(json.dumps(request_payload))
            first = json.loads(websocket.receive_text())
            second = json.loads(websocket.receive_text())

    assert first["type"] == "response.created"
    assert second["type"] == "response.completed"
    sent_payload = json.loads(fake_upstream.sent_text[0])
    assert sent_payload["previous_response_id"] == "resp_prev_tool_call"
    assert sent_payload["input"] == [
        {
            "type": "function_call_output",
            "call_id": "call_repeat",
            "output": "Wed May 6 16:00:00 UTC 2026",
        }
    ]


def test_v1_responses_websocket_forwards_previous_response_id(app_instance, monkeypatch):
    fake_upstream = _FakeUpstreamWebSocket(
        [
            _FakeUpstreamMessage(
                "text",
                text=json.dumps(
                    {"type": "response.created", "response": {"id": "resp_ws_v1_prev", "status": "in_progress"}},
                    separators=(",", ":"),
                ),
            ),
            _FakeUpstreamMessage(
                "text",
                text=json.dumps(
                    {"type": "response.completed", "response": {"id": "resp_ws_v1_prev", "status": "completed"}},
                    separators=(",", ":"),
                ),
            ),
        ]
    )
    seen: dict[str, object] = {}

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
        prefer_earlier_reset,
        prefer_earlier_reset_window,
        routing_strategy,
        model,
        request_state,
        api_key,
        client_send_lock,
        websocket,
        reallocate_sticky=False,
        sticky_max_age_seconds=None,
    ):
        del self, headers, sticky_key, sticky_kind, prefer_earlier_reset, routing_strategy, model
        del request_state, api_key, client_send_lock, websocket, reallocate_sticky, sticky_max_age_seconds
        seen["connected"] = True
        return SimpleNamespace(id="acct_ws_v1_prev"), fake_upstream

    async def fake_write_request_log(self, **kwargs):
        del self, kwargs

    monkeypatch.setattr(proxy_api_module, "_websocket_firewall_denial_response", allow_firewall)
    monkeypatch.setattr(proxy_api_module, "validate_proxy_api_key_authorization", allow_proxy_api_key)
    monkeypatch.setattr(proxy_module, "get_settings_cache", lambda: _FakeSettingsCache())
    monkeypatch.setattr(proxy_module.ProxyService, "_connect_proxy_websocket", fake_connect_proxy_websocket)
    monkeypatch.setattr(proxy_module.ProxyService, "_write_request_log", fake_write_request_log)

    request_payload = {
        "type": "response.create",
        "model": "gpt-5.4",
        "input": "continue",
        "previous_response_id": "resp_prev_v1_123",
        "stream": True,
    }

    with TestClient(app_instance) as client:
        with client.websocket_connect("/v1/responses") as websocket:
            websocket.send_text(json.dumps(request_payload))
            first = json.loads(websocket.receive_text())
            second = json.loads(websocket.receive_text())

    assert seen["connected"] is True
    assert first["type"] == "response.created"
    assert second["type"] == "response.completed"
    assert [json.loads(message) for message in fake_upstream.sent_text] == [
        {
            "model": "gpt-5.4",
            "instructions": "",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "continue"}]}],
            "tools": [],
            "store": False,
            "include": [],
            "previous_response_id": "resp_prev_v1_123",
            "type": "response.create",
        }
    ]
