from __future__ import annotations
import json
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
import app.modules.proxy.api as proxy_api_module
import app.modules.proxy.service as proxy_module
from tests.integration.proxy_websocket_responses_support import (
    _FailingSendUpstreamWebSocket,
    _FakeUpstreamMessage,
    _FakeUpstreamWebSocket,
    _stub_request_logging,
    _websocket_settings,
)

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("frame", ['{"type":"response.create"', "[]"])
def test_backend_responses_websocket_rejects_malformed_first_frame_as_invalid_payload(app_instance, monkeypatch, frame):
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
        raise AssertionError("malformed initial websocket frame must not open upstream")

    monkeypatch.setattr(proxy_api_module, "_websocket_firewall_denial_response", allow_firewall)
    monkeypatch.setattr(proxy_api_module, "validate_proxy_api_key_authorization", allow_proxy_api_key)
    monkeypatch.setattr(proxy_module, "get_settings_cache", lambda: _FakeSettingsCache())
    monkeypatch.setattr(proxy_module.ProxyService, "_connect_proxy_websocket", fail_connect_proxy_websocket)

    with TestClient(app_instance) as client:
        with client.websocket_connect("/backend-api/codex/responses") as websocket:
            websocket.send_text(frame)
            event = json.loads(websocket.receive_text())

    assert called["connect"] is False
    assert event["type"] == "error"
    assert event["status"] == 400
    assert event["error"]["type"] == "invalid_request_error"
    assert event["error"]["message"] == "Invalid request payload"


def test_backend_responses_websocket_emits_timeout_failure_for_stalled_upstream(app_instance, monkeypatch):
    fake_upstream = _FakeUpstreamWebSocket(
        [
            _FakeUpstreamMessage(
                "text",
                text=json.dumps(
                    {"type": "response.created", "response": {"id": "resp_ws_idle", "status": "in_progress"}},
                    separators=(",", ":"),
                ),
            ),
        ]
    )
    log_calls: list[dict[str, object]] = []
    handled_error_codes: list[str] = []
    connect_attempts = {"count": 0}

    class _FakeSettingsCache:
        async def get(self):
            return _websocket_settings()

    runtime_settings = _websocket_settings(
        proxy_request_budget_seconds=5.0,
        stream_idle_timeout_seconds=0.01,
    )

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
        del self, headers, sticky_key, sticky_kind, reallocate_sticky, sticky_max_age_seconds
        del prefer_earlier_reset, routing_strategy, model, api_key
        connect_attempts["count"] += 1
        if connect_attempts["count"] == 1:
            del client_send_lock, websocket, request_state
            return (
                proxy_module.Account(
                    id="acct_ws_proxy",
                    chatgpt_account_id="acct_ws_proxy",
                    email="acct_ws_proxy@example.com",
                    plan_type="plus",
                    access_token_encrypted=b"access",
                    refresh_token_encrypted=b"refresh",
                    id_token_encrypted=b"id",
                    last_refresh=proxy_module.utcnow(),
                    status=proxy_module.AccountStatus.ACTIVE,
                ),
                fake_upstream,
            )
        async with client_send_lock:
            await websocket.send_text(json.dumps({"type": "error", "status": 503, "error": {"code": "no_accounts"}}))
        return None, None

    async def fake_write_request_log(self, **kwargs):
        del self
        log_calls.append(kwargs)

    async def fake_handle_stream_error(self, account, error, code):
        del self, account, error
        handled_error_codes.append(code)

    monkeypatch.setattr(proxy_api_module, "_websocket_firewall_denial_response", allow_firewall)
    monkeypatch.setattr(proxy_api_module, "validate_proxy_api_key_authorization", allow_proxy_api_key)
    monkeypatch.setattr(proxy_module, "get_settings_cache", lambda: _FakeSettingsCache())
    monkeypatch.setattr(proxy_module, "get_settings", lambda: runtime_settings)
    monkeypatch.setattr(proxy_module.ProxyService, "_connect_proxy_websocket", fake_connect_proxy_websocket)
    monkeypatch.setattr(proxy_module.ProxyService, "_handle_stream_error", fake_handle_stream_error)
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
            failed_event = json.loads(websocket.receive_text())

            websocket.send_text(json.dumps(request_payload))
            followup_event = json.loads(websocket.receive_text())

    assert created_event["type"] == "response.created"
    assert failed_event["type"] == "response.failed"
    assert failed_event["response"]["id"] == "resp_ws_idle"
    assert failed_event["response"]["error"]["code"] == "stream_idle_timeout"
    assert failed_event["response"]["error"]["message"] == "Upstream stream idle timeout"
    assert fake_upstream.closed is True
    assert connect_attempts["count"] == 2
    assert handled_error_codes == ["stream_idle_timeout"]
    assert followup_event["type"] == "error"
    assert followup_event["status"] == 503
    assert followup_event["error"]["code"] == "no_accounts"
    assert len(log_calls) == 1
    assert log_calls[0]["request_id"] == "resp_ws_idle"
    assert log_calls[0]["error_code"] == "stream_idle_timeout"
    assert log_calls[0]["error_message"] == "Upstream stream idle timeout"


def test_backend_responses_websocket_treats_typeless_upstream_error_as_terminal(app_instance, monkeypatch):
    fake_upstream = _FakeUpstreamWebSocket(
        [
            _FakeUpstreamMessage(
                "text",
                text=json.dumps(
                    {"type": "response.created", "response": {"id": "resp_ws_raw_error", "status": "in_progress"}},
                    separators=(",", ":"),
                ),
            ),
            _FakeUpstreamMessage(
                "text",
                text=json.dumps(
                    {
                        "error": {
                            "type": "invalid_request_error",
                            "message": "No tool output found for function call call_missing.",
                            "param": "input",
                        },
                        "status": 400,
                    },
                    separators=(",", ":"),
                ),
            ),
        ]
    )
    log_calls: list[dict[str, object]] = []
    connect_attempts = {"count": 0}

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
        del self, headers, sticky_key, sticky_kind, reallocate_sticky, sticky_max_age_seconds
        del prefer_earlier_reset, routing_strategy, model, request_state, api_key
        del client_send_lock, websocket
        connect_attempts["count"] += 1
        return SimpleNamespace(id="acct_ws_proxy"), fake_upstream

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
            error_event = json.loads(websocket.receive_text())

    assert created_event["type"] == "response.created"
    assert error_event["status"] == 400
    assert error_event["error"]["type"] == "invalid_request_error"
    assert error_event["error"]["param"] == "input"
    assert connect_attempts["count"] == 1
    assert len(log_calls) == 1
    assert log_calls[0]["request_id"] == "resp_ws_raw_error"
    assert log_calls[0]["status"] == "error"
    assert log_calls[0]["error_code"] == "invalid_request_error"
    assert log_calls[0]["error_message"] == "No tool output found for function call call_missing."


def test_backend_responses_websocket_emits_terminal_failure_when_upstream_send_breaks(app_instance, monkeypatch):
    fake_upstream = _FailingSendUpstreamWebSocket([])
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
        return SimpleNamespace(id="acct_ws_proxy"), fake_upstream

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
            failed_event = json.loads(websocket.receive_text())

    assert failed_event["type"] == "response.failed"
    assert failed_event["response"]["error"]["code"] == "stream_incomplete"
    assert failed_event["response"]["error"]["message"] == "Upstream websocket closed before response.completed"
    assert len(log_calls) == 1
    assert log_calls[0]["error_code"] == "stream_incomplete"
    assert log_calls[0]["status"] == "error"
