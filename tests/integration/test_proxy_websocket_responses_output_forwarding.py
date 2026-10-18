from __future__ import annotations
import json
from types import SimpleNamespace
from typing import cast
import pytest
from fastapi.testclient import TestClient
import app.modules.proxy.api as proxy_api_module
import app.modules.proxy.service as proxy_module
from app.core.utils.client_session import get_client_session_id
from tests.integration.proxy_websocket_responses_support import (
    _FakeUpstreamMessage,
    _FakeUpstreamWebSocket,
    _stub_request_logging,
    _websocket_settings,
)

pytestmark = pytest.mark.integration


def test_backend_responses_websocket_proxies_upstream_and_persists_log(app_instance, monkeypatch):
    upstream_messages = [
        _FakeUpstreamMessage(
            "text",
            text=json.dumps(
                {
                    "type": "response.created",
                    "response": {"id": "resp_ws_1", "object": "response", "status": "in_progress"},
                },
                separators=(",", ":"),
            ),
        ),
        _FakeUpstreamMessage(
            "text",
            text=json.dumps(
                {
                    "type": "response.completed",
                    "response": {
                        "id": "resp_ws_1",
                        "object": "response",
                        "status": "completed",
                        "service_tier": "fast",
                        "usage": {"input_tokens": 3, "output_tokens": 5, "total_tokens": 8},
                    },
                },
                separators=(",", ":"),
            ),
        ),
    ]
    fake_upstream = _FakeUpstreamWebSocket(upstream_messages)
    seen: dict[str, object] = {}
    log_calls: list[dict[str, object]] = []

    class _FakeSettingsCache:
        async def get(self):
            return _websocket_settings()

    async def allow_firewall(_websocket):
        return None

    async def allow_proxy_api_key(authorization: str | None, *, request: object | None = None):
        assert authorization == "Bearer external-token"
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
        del api_key, reallocate_sticky, sticky_max_age_seconds
        seen["headers"] = dict(headers)
        seen["sticky_key"] = sticky_key
        seen["sticky_kind"] = sticky_kind
        seen["prefer_earlier_reset"] = prefer_earlier_reset
        seen["routing_strategy"] = routing_strategy
        seen["model"] = model
        seen["request_id"] = request_state.request_id
        return SimpleNamespace(id="acct_ws_proxy"), fake_upstream

    async def fake_write_request_log(self, **kwargs):

        log_calls.append({**kwargs, "client_session_id": get_client_session_id()})

    monkeypatch.setattr(proxy_api_module, "_websocket_firewall_denial_response", allow_firewall)
    monkeypatch.setattr(proxy_api_module, "validate_proxy_api_key_authorization", allow_proxy_api_key)
    monkeypatch.setattr(proxy_module, "get_settings_cache", lambda: _FakeSettingsCache())
    monkeypatch.setattr(proxy_module.ProxyService, "_connect_proxy_websocket", fake_connect_proxy_websocket)
    monkeypatch.setattr(proxy_module.ProxyService, "_write_request_log", fake_write_request_log)

    request_payload = {
        "type": "response.create",
        "model": "gpt-5.4",
        "instructions": "",
        "client_metadata": {"x-codex-turn-metadata": '{"turn_id":"turn_123","sandbox":"workspace-write"}'},
        "service_tier": "fast",
        "reasoning": {"effort": "high"},
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}],
        "stream": True,
    }

    with TestClient(app_instance) as client:
        with client.websocket_connect(
            "/backend-api/codex/responses",
            headers={
                "Authorization": "Bearer external-token",
                "chatgpt-account-id": "external-account",
                "session_id": "thread-ws-1",
                "openai-beta": "responses_websockets=2026-02-06",
                "Tailscale-User-Login": "fake@example.invalid",
                "Tailscale-User-Name": "Fake Member",
                "Tailscale-User-Profile-Pic": "https://example.invalid/pic",
                "X-Agent-LB-Machine": "box-1",
            },
        ) as websocket:
            websocket.send_text(json.dumps(request_payload))
            first = json.loads(websocket.receive_text())
            second = json.loads(websocket.receive_text())

    assert first["type"] == "response.created"
    assert second["type"] == "response.completed"
    seen_headers = cast(dict[str, str], seen["headers"])
    assert seen_headers["session_id"] == "thread-ws-1"
    assert not any(key.lower().startswith("tailscale-") or key.lower() == "x-agent-lb-machine" for key in seen_headers)
    assert seen_headers["openai-beta"] == "responses_websockets=2026-02-06"
    assert seen_headers["x-codex-turn-state"] == cast(str, seen["sticky_key"])
    assert seen["sticky_kind"] == proxy_module.StickySessionKind.CODEX_SESSION
    assert seen["prefer_earlier_reset"] is False
    assert seen["routing_strategy"] == "usage_weighted"
    assert seen["model"] == "gpt-5.4"
    assert [json.loads(message) for message in fake_upstream.sent_text] == [
        {
            "model": "gpt-5.4",
            "instructions": "",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}],
            "tools": [],
            "reasoning": {"effort": "high"},
            "client_metadata": {"x-codex-turn-metadata": '{"turn_id":"turn_123","sandbox":"workspace-write"}'},
            "service_tier": "priority",
            "store": False,
            "include": [],
            "type": "response.create",
        }
    ]
    assert len(log_calls) == 1
    log = log_calls[0]
    assert log["account_id"] == "acct_ws_proxy"
    assert log["request_id"] == "resp_ws_1"
    assert log["model"] == "gpt-5.4"
    assert log["service_tier"] == "priority"
    assert log["transport"] == "websocket"
    assert log["status"] == "success"
    assert log["input_tokens"] == 3
    assert log["output_tokens"] == 5
    assert log["client_session_id"] == "thread-ws-1"


def test_backend_responses_websocket_keeps_same_response_distinct_tool_call_ids(
    app_instance,
    monkeypatch,
):
    duplicate_arguments = json.dumps(
        {"session_id": 41288, "chars": "", "yield_time_ms": 30000, "max_output_tokens": 6000},
        separators=(",", ":"),
    )
    upstream_messages = [
        _FakeUpstreamMessage(
            "text",
            text=json.dumps(
                {
                    "type": "response.created",
                    "response": {"id": "resp_ws_duplicate_tool", "status": "in_progress"},
                },
                separators=(",", ":"),
            ),
        ),
        _FakeUpstreamMessage(
            "text",
            text=json.dumps(
                {
                    "type": "response.output_item.done",
                    "item": {
                        "id": "fc_first",
                        "type": "function_call",
                        "status": "completed",
                        "arguments": duplicate_arguments,
                        "call_id": "call_first",
                        "name": "write_stdin",
                    },
                    "output_index": 0,
                },
                separators=(",", ":"),
            ),
        ),
        _FakeUpstreamMessage(
            "text",
            text=json.dumps(
                {
                    "type": "response.output_item.done",
                    "item": {
                        "id": "fc_replay",
                        "type": "function_call",
                        "status": "completed",
                        "arguments": duplicate_arguments,
                        "call_id": "call_replay",
                        "name": "write_stdin",
                    },
                    "output_index": 0,
                },
                separators=(",", ":"),
            ),
        ),
        _FakeUpstreamMessage(
            "text",
            text=json.dumps(
                {
                    "type": "response.completed",
                    "response": {
                        "id": "resp_ws_duplicate_tool",
                        "status": "completed",
                        "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                    },
                },
                separators=(",", ":"),
            ),
        ),
    ]
    fake_upstream = _FakeUpstreamWebSocket(upstream_messages)
    log_calls: list[dict[str, object]] = []

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
        del (
            self,
            headers,
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
        )
        return SimpleNamespace(id="acct_ws_duplicate_tool"), fake_upstream

    async def fake_write_request_log(self, **kwargs):
        del self
        log_calls.append(kwargs)

    monkeypatch.setattr(proxy_api_module, "_websocket_firewall_denial_response", allow_firewall)
    monkeypatch.setattr(proxy_api_module, "validate_proxy_api_key_authorization", allow_proxy_api_key)
    monkeypatch.setattr(proxy_module, "get_settings_cache", lambda: _FakeSettingsCache())
    monkeypatch.setattr(proxy_module.ProxyService, "_connect_proxy_websocket", fake_connect_proxy_websocket)
    monkeypatch.setattr(proxy_module.ProxyService, "_write_request_log", fake_write_request_log)

    request_payload = {
        "type": "response.create",
        "model": "gpt-5.4",
        "instructions": "",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}],
        "stream": True,
    }

    with TestClient(app_instance) as client:
        with client.websocket_connect("/backend-api/codex/responses") as websocket:
            websocket.send_text(json.dumps(request_payload))
            created_event = json.loads(websocket.receive_text())
            tool_event = json.loads(websocket.receive_text())
            replay_tool_event = json.loads(websocket.receive_text())
            terminal_event = json.loads(websocket.receive_text())

    assert created_event["type"] == "response.created"
    assert tool_event["type"] == "response.output_item.done"
    assert tool_event["item"]["call_id"] == "call_first"
    assert replay_tool_event["type"] == "response.output_item.done"
    assert replay_tool_event["item"]["call_id"] == "call_replay"
    assert terminal_event["type"] == "response.completed"
    assert terminal_event["response"]["id"] == "resp_ws_duplicate_tool"
    assert len(log_calls) == 1
    assert log_calls[0]["status"] == "success"


def test_backend_responses_websocket_preserves_image_generation_tool_advertisement(app_instance, monkeypatch):
    upstream_messages = [
        _FakeUpstreamMessage(
            "text",
            text=json.dumps(
                {
                    "type": "response.created",
                    "response": {"id": "resp_ws_tools", "object": "response", "status": "in_progress"},
                },
                separators=(",", ":"),
            ),
        ),
        _FakeUpstreamMessage(
            "text",
            text=json.dumps(
                {
                    "type": "response.completed",
                    "response": {
                        "id": "resp_ws_tools",
                        "object": "response",
                        "status": "completed",
                        "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                    },
                },
                separators=(",", ":"),
            ),
        ),
    ]
    fake_upstream = _FakeUpstreamWebSocket(upstream_messages)

    class _FakeSettingsCache:
        async def get(self):
            return _websocket_settings()

    async def allow_firewall(_websocket):
        return None

    async def allow_proxy_api_key(authorization: str | None, *, request: object | None = None):
        assert authorization == "Bearer external-token"
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
        del (
            self,
            headers,
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
        )
        return SimpleNamespace(id="acct_ws_proxy"), fake_upstream

    monkeypatch.setattr(proxy_api_module, "_websocket_firewall_denial_response", allow_firewall)
    monkeypatch.setattr(proxy_api_module, "validate_proxy_api_key_authorization", allow_proxy_api_key)
    monkeypatch.setattr(proxy_module, "get_settings_cache", lambda: _FakeSettingsCache())
    monkeypatch.setattr(proxy_module.ProxyService, "_connect_proxy_websocket", fake_connect_proxy_websocket)

    function_tool = {
        "type": "function",
        "name": "lookup_weather",
        "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
    }
    request_payload = {
        "type": "response.create",
        "model": "gpt-5.4",
        "instructions": "",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}],
        "tools": [{"type": "image_generation", "output_format": "png"}, function_tool],
        "stream": True,
    }

    with TestClient(app_instance) as client:
        with client.websocket_connect(
            "/backend-api/codex/responses",
            headers={"Authorization": "Bearer external-token"},
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
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}],
            "tools": [{"type": "image_generation", "output_format": "png"}, function_tool],
            "store": False,
            "include": [],
            "type": "response.create",
        }
    ]
