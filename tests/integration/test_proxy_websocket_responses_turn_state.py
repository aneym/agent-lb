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


def test_backend_responses_websocket_accepts_and_reuses_generated_turn_state(app_instance, monkeypatch):
    upstream_messages = [
        _FakeUpstreamMessage(
            "text",
            text=json.dumps(
                {"type": "response.created", "response": {"id": "resp_turn_state", "status": "in_progress"}},
                separators=(",", ":"),
            ),
        ),
        _FakeUpstreamMessage(
            "text",
            text=json.dumps(
                {
                    "type": "response.completed",
                    "response": {
                        "id": "resp_turn_state",
                        "status": "completed",
                        "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
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
        seen["headers"] = dict(headers)
        seen["sticky_key"] = sticky_key
        seen["sticky_kind"] = sticky_kind
        return SimpleNamespace(id="acct_turn_state"), fake_upstream

    monkeypatch.setattr(proxy_api_module, "_websocket_firewall_denial_response", allow_firewall)
    monkeypatch.setattr(proxy_api_module, "validate_proxy_api_key_authorization", allow_proxy_api_key)
    monkeypatch.setattr(proxy_module, "get_settings_cache", lambda: _FakeSettingsCache())
    monkeypatch.setattr(proxy_module.ProxyService, "_connect_proxy_websocket", fake_connect_proxy_websocket)

    request_payload = {
        "type": "response.create",
        "model": "gpt-5.4",
        "instructions": "",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}],
        "stream": True,
    }

    with TestClient(app_instance) as client:
        with client.websocket_connect(
            "/backend-api/codex/responses",
            headers={"Authorization": "Bearer external-token"},
        ) as websocket:
            raw_extra_headers = cast(list[tuple[bytes, bytes]], websocket.extra_headers)
            extra_headers = {key.decode(): value.decode() for key, value in raw_extra_headers}
            turn_state = extra_headers["x-codex-turn-state"]
            websocket.send_text(json.dumps(request_payload))
            completed = json.loads(websocket.receive_text())
            assert completed["type"] == "response.created"
            _ = json.loads(websocket.receive_text())

    seen_headers = cast(dict[str, str], seen["headers"])
    assert turn_state
    assert seen_headers["x-codex-turn-state"] == turn_state
    assert seen["sticky_key"] == turn_state
    assert seen["sticky_kind"] == proxy_module.StickySessionKind.CODEX_SESSION


def test_backend_responses_websocket_echoes_existing_turn_state_header(app_instance, monkeypatch):
    fake_upstream = _FakeUpstreamWebSocket(
        [
            _FakeUpstreamMessage(
                "text",
                text=json.dumps(
                    {"type": "response.created", "response": {"id": "resp_existing_turn", "status": "in_progress"}},
                    separators=(",", ":"),
                ),
            ),
            _FakeUpstreamMessage(
                "text",
                text=json.dumps(
                    {
                        "type": "response.completed",
                        "response": {
                            "id": "resp_existing_turn",
                            "status": "completed",
                            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                        },
                    },
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
            reallocate_sticky,
            sticky_max_age_seconds,
            prefer_earlier_reset,
            routing_strategy,
            model,
            request_state,
            api_key,
            client_send_lock,
            websocket,
        )
        seen["headers"] = dict(headers)
        seen["sticky_key"] = sticky_key
        seen["sticky_kind"] = sticky_kind
        return SimpleNamespace(id="acct_turn_state"), fake_upstream

    monkeypatch.setattr(proxy_api_module, "_websocket_firewall_denial_response", allow_firewall)
    monkeypatch.setattr(proxy_api_module, "validate_proxy_api_key_authorization", allow_proxy_api_key)
    monkeypatch.setattr(proxy_module, "get_settings_cache", lambda: _FakeSettingsCache())
    monkeypatch.setattr(proxy_module.ProxyService, "_connect_proxy_websocket", fake_connect_proxy_websocket)

    request_payload = {
        "type": "response.create",
        "model": "gpt-5.4",
        "instructions": "",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}],
        "stream": True,
    }
    existing_turn_state = "turn_state_existing_123"

    with TestClient(app_instance) as client:
        with client.websocket_connect(
            "/backend-api/codex/responses",
            headers={
                "Authorization": "Bearer external-token",
                "x-codex-turn-state": existing_turn_state,
            },
        ) as websocket:
            raw_extra_headers = cast(list[tuple[bytes, bytes]], websocket.extra_headers)
            extra_headers = {key.decode(): value.decode() for key, value in raw_extra_headers}
            assert extra_headers["x-codex-turn-state"] == existing_turn_state
            websocket.send_text(json.dumps(request_payload))
            _ = json.loads(websocket.receive_text())
            _ = json.loads(websocket.receive_text())

    seen_headers = cast(dict[str, str], seen["headers"])
    assert seen_headers["x-codex-turn-state"] == existing_turn_state
    assert seen["sticky_key"] == existing_turn_state
    assert seen["sticky_kind"] == proxy_module.StickySessionKind.CODEX_SESSION


def test_v1_responses_websocket_accepts_and_reuses_generated_turn_state(app_instance, monkeypatch):
    fake_upstream = _FakeUpstreamWebSocket(
        [
            _FakeUpstreamMessage(
                "text",
                text=json.dumps(
                    {"type": "response.created", "response": {"id": "resp_v1_turn_state", "status": "in_progress"}},
                    separators=(",", ":"),
                ),
            ),
            _FakeUpstreamMessage(
                "text",
                text=json.dumps(
                    {
                        "type": "response.completed",
                        "response": {
                            "id": "resp_v1_turn_state",
                            "status": "completed",
                            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                        },
                    },
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
        seen["headers"] = dict(headers)
        seen["sticky_key"] = sticky_key
        seen["sticky_kind"] = sticky_kind
        return SimpleNamespace(id="acct_v1_turn_state"), fake_upstream

    monkeypatch.setattr(proxy_api_module, "_websocket_firewall_denial_response", allow_firewall)
    monkeypatch.setattr(proxy_api_module, "validate_proxy_api_key_authorization", allow_proxy_api_key)
    monkeypatch.setattr(proxy_module, "get_settings_cache", lambda: _FakeSettingsCache())
    monkeypatch.setattr(proxy_module.ProxyService, "_connect_proxy_websocket", fake_connect_proxy_websocket)

    request_payload = {
        "type": "response.create",
        "model": "gpt-5.4",
        "input": "hi",
        "stream": True,
    }

    with TestClient(app_instance) as client:
        with client.websocket_connect("/v1/responses") as websocket:
            raw_extra_headers = cast(list[tuple[bytes, bytes]], websocket.extra_headers)
            extra_headers = {key.decode(): value.decode() for key, value in raw_extra_headers}
            turn_state = extra_headers["x-codex-turn-state"]
            websocket.send_text(json.dumps(request_payload))
            created = json.loads(websocket.receive_text())
            assert created["type"] == "response.created"
            _ = json.loads(websocket.receive_text())

    seen_headers = cast(dict[str, str], seen["headers"])
    assert turn_state
    assert seen_headers["x-codex-turn-state"] == turn_state
    assert seen["sticky_key"] == turn_state
    assert seen["sticky_kind"] == proxy_module.StickySessionKind.CODEX_SESSION
