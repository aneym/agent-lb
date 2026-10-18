from __future__ import annotations
import json
from types import SimpleNamespace
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


def test_backend_responses_websocket_reconnects_after_account_health_failure(app_instance, monkeypatch):
    first_upstream = _FakeUpstreamWebSocket(
        [
            _FakeUpstreamMessage(
                "text",
                text=json.dumps(
                    {"type": "response.created", "response": {"id": "resp_ws_fail", "status": "in_progress"}},
                    separators=(",", ":"),
                ),
            ),
            _FakeUpstreamMessage(
                "text",
                text=json.dumps(
                    {
                        "type": "response.failed",
                        "response": {
                            "id": "resp_ws_fail",
                            "status": "failed",
                            "error": {"code": "rate_limit_exceeded", "message": "slow down"},
                            "usage": {"input_tokens": 1, "output_tokens": 0, "total_tokens": 1},
                        },
                    },
                    separators=(",", ":"),
                ),
            ),
        ]
    )
    second_upstream = _FakeUpstreamWebSocket(
        [
            _FakeUpstreamMessage(
                "text",
                text=json.dumps(
                    {"type": "response.created", "response": {"id": "resp_ws_ok", "status": "in_progress"}},
                    separators=(",", ":"),
                ),
            ),
            _FakeUpstreamMessage(
                "text",
                text=json.dumps(
                    {
                        "type": "response.completed",
                        "response": {
                            "id": "resp_ws_ok",
                            "status": "completed",
                            "usage": {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
                        },
                    },
                    separators=(",", ":"),
                ),
            ),
        ]
    )
    upstreams = [first_upstream, second_upstream]
    connect_models: list[str | None] = []
    handled_error_codes: list[str] = []

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
            request_state,
            api_key,
            client_send_lock,
            websocket,
        )
        upstream = upstreams[len(connect_models)]
        connect_models.append(model)
        return SimpleNamespace(id=f"acct_ws_proxy_{len(connect_models)}"), upstream

    async def fake_handle_stream_error(self, account, error, code):
        del self, account, error
        handled_error_codes.append(code)

    async def fake_write_request_log(self, **kwargs):
        del self, kwargs

    monkeypatch.setattr(proxy_api_module, "_websocket_firewall_denial_response", allow_firewall)
    monkeypatch.setattr(proxy_api_module, "validate_proxy_api_key_authorization", allow_proxy_api_key)
    monkeypatch.setattr(proxy_module, "get_settings_cache", lambda: _FakeSettingsCache())
    monkeypatch.setattr(proxy_module.ProxyService, "_connect_proxy_websocket", fake_connect_proxy_websocket)
    monkeypatch.setattr(proxy_module.ProxyService, "_handle_stream_error", fake_handle_stream_error)
    monkeypatch.setattr(proxy_module.ProxyService, "_write_request_log", fake_write_request_log)

    first_request = {
        "type": "response.create",
        "model": "gpt-5.1",
        "instructions": "",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "first"}]}],
        "stream": True,
    }
    second_request = {
        "type": "response.create",
        "model": "gpt-5.2",
        "instructions": "",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "second"}]}],
        "stream": True,
    }

    with TestClient(app_instance) as client:
        with client.websocket_connect("/backend-api/codex/responses") as websocket:
            websocket.send_text(json.dumps(first_request))
            failed_events = [json.loads(websocket.receive_text()) for _ in range(2)]

            websocket.send_text(json.dumps(second_request))
            success_events = [json.loads(websocket.receive_text()) for _ in range(2)]

    assert [event["type"] for event in failed_events] == ["response.created", "response.failed"]
    assert failed_events[1]["response"]["error"]["code"] == "rate_limit_exceeded"
    assert [event["type"] for event in success_events] == ["response.created", "response.completed"]
    assert connect_models == ["gpt-5.1", "gpt-5.2"]
    assert handled_error_codes == ["rate_limit_exceeded"]
    assert first_upstream.closed is True
    assert [json.loads(message) for message in first_upstream.sent_text] == [
        {
            "model": "gpt-5.1",
            "instructions": "",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "first"}]}],
            "tools": [],
            "store": False,
            "include": [],
            "type": "response.create",
        }
    ]
    assert [json.loads(message) for message in second_upstream.sent_text] == [
        {
            "model": "gpt-5.2",
            "instructions": "",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "second"}]}],
            "tools": [],
            "store": False,
            "include": [],
            "type": "response.create",
        }
    ]


def test_backend_responses_websocket_transparently_retries_precreated_usage_limit_reached(app_instance, monkeypatch):
    first_upstream = _FakeUpstreamWebSocket(
        [
            _FakeUpstreamMessage(
                "text",
                text=json.dumps(
                    {
                        "type": "response.failed",
                        "response": {
                            "id": "resp_ws_quota_fail",
                            "status": "failed",
                            "error": {"code": "usage_limit_reached", "message": "usage limit reached"},
                            "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
                        },
                    },
                    separators=(",", ":"),
                ),
            )
        ]
    )
    second_upstream = _FakeUpstreamWebSocket(
        [
            _FakeUpstreamMessage(
                "text",
                text=json.dumps(
                    {"type": "response.created", "response": {"id": "resp_ws_quota_ok", "status": "in_progress"}},
                    separators=(",", ":"),
                ),
            ),
            _FakeUpstreamMessage(
                "text",
                text=json.dumps(
                    {
                        "type": "response.completed",
                        "response": {
                            "id": "resp_ws_quota_ok",
                            "status": "completed",
                            "usage": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
                        },
                    },
                    separators=(",", ":"),
                ),
            ),
        ]
    )
    upstreams = [first_upstream, second_upstream]
    connect_models: list[str | None] = []
    handled_error_codes: list[str] = []

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
            request_state,
            api_key,
            client_send_lock,
            websocket,
        )
        upstream = upstreams[len(connect_models)]
        connect_models.append(model)
        return SimpleNamespace(id=f"acct_ws_proxy_{len(connect_models)}"), upstream

    async def fake_handle_stream_error(self, account, error, code):
        del self, account, error
        handled_error_codes.append(code)

    async def fake_write_request_log(self, **kwargs):
        del self, kwargs

    monkeypatch.setattr(proxy_api_module, "_websocket_firewall_denial_response", allow_firewall)
    monkeypatch.setattr(proxy_api_module, "validate_proxy_api_key_authorization", allow_proxy_api_key)
    monkeypatch.setattr(proxy_module, "get_settings_cache", lambda: _FakeSettingsCache())
    monkeypatch.setattr(proxy_module.ProxyService, "_connect_proxy_websocket", fake_connect_proxy_websocket)
    monkeypatch.setattr(proxy_module.ProxyService, "_handle_stream_error", fake_handle_stream_error)
    monkeypatch.setattr(proxy_module.ProxyService, "_write_request_log", fake_write_request_log)

    request_payload = {
        "type": "response.create",
        "model": "gpt-5.1",
        "instructions": "",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "retry once"}]}],
        "stream": True,
    }

    with TestClient(app_instance) as client:
        with client.websocket_connect("/backend-api/codex/responses") as websocket:
            websocket.send_text(json.dumps(request_payload))
            first_event = json.loads(websocket.receive_text())
            assert first_event["type"] == "response.created"
            second_event = json.loads(websocket.receive_text())

    assert second_event["type"] == "response.completed"
    assert connect_models == ["gpt-5.1", "gpt-5.1"]
    assert handled_error_codes == ["usage_limit_reached"]
    assert len(first_upstream.sent_text) == 1
    assert len(second_upstream.sent_text) == 1
    assert json.loads(first_upstream.sent_text[0]) == json.loads(second_upstream.sent_text[0])


def test_backend_responses_websocket_transparently_retries_precreated_error_usage_limit_reached(
    app_instance,
    monkeypatch,
):
    first_upstream = _FakeUpstreamWebSocket(
        [
            _FakeUpstreamMessage(
                "text",
                text=json.dumps(
                    {
                        "type": "error",
                        "status": 429,
                        "error": {
                            "type": "invalid_request_error",
                            "code": "usage_limit_reached",
                            "message": "The usage limit has been reached",
                        },
                    },
                    separators=(",", ":"),
                ),
            )
        ]
    )
    second_upstream = _FakeUpstreamWebSocket(
        [
            _FakeUpstreamMessage(
                "text",
                text=json.dumps(
                    {"type": "response.created", "response": {"id": "resp_ws_quota_ok_err", "status": "in_progress"}},
                    separators=(",", ":"),
                ),
            ),
            _FakeUpstreamMessage(
                "text",
                text=json.dumps(
                    {
                        "type": "response.completed",
                        "response": {
                            "id": "resp_ws_quota_ok_err",
                            "status": "completed",
                            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                        },
                    },
                    separators=(",", ":"),
                ),
            ),
        ]
    )
    upstreams = [first_upstream, second_upstream]
    connect_models: list[str | None] = []
    handled_error_codes: list[str] = []

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
            request_state,
            api_key,
            client_send_lock,
            websocket,
        )
        upstream = upstreams[len(connect_models)]
        connect_models.append(model)
        return SimpleNamespace(id=f"acct_ws_proxy_{len(connect_models)}"), upstream

    async def fake_handle_stream_error(self, account, error, code):
        del self, account, error
        handled_error_codes.append(code)

    async def fake_write_request_log(self, **kwargs):
        del self, kwargs

    monkeypatch.setattr(proxy_api_module, "_websocket_firewall_denial_response", allow_firewall)
    monkeypatch.setattr(proxy_api_module, "validate_proxy_api_key_authorization", allow_proxy_api_key)
    monkeypatch.setattr(proxy_module, "get_settings_cache", lambda: _FakeSettingsCache())
    monkeypatch.setattr(proxy_module.ProxyService, "_connect_proxy_websocket", fake_connect_proxy_websocket)
    monkeypatch.setattr(proxy_module.ProxyService, "_handle_stream_error", fake_handle_stream_error)
    monkeypatch.setattr(proxy_module.ProxyService, "_write_request_log", fake_write_request_log)

    request_payload = {
        "type": "response.create",
        "model": "gpt-5.1",
        "instructions": "",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "retry once"}]}],
        "stream": True,
    }

    with TestClient(app_instance) as client:
        with client.websocket_connect("/backend-api/codex/responses") as websocket:
            websocket.send_text(json.dumps(request_payload))
            first_event = json.loads(websocket.receive_text())
            second_event = json.loads(websocket.receive_text())

    assert first_event["type"] == "response.created"
    assert second_event["type"] == "response.completed"
    assert connect_models == ["gpt-5.1", "gpt-5.1"]
    assert handled_error_codes == ["usage_limit_reached"]
    assert len(first_upstream.sent_text) == 1
    assert len(second_upstream.sent_text) == 1
    assert json.loads(first_upstream.sent_text[0]) == json.loads(second_upstream.sent_text[0])
