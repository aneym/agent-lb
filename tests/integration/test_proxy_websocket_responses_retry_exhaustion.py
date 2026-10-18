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


@pytest.mark.parametrize(
    ("endpoint", "expected_code", "expected_message"),
    [
        (
            "/backend-api/codex/responses",
            proxy_module.PREVIOUS_RESPONSE_STALE_CODE,
            "Previous response owner account reached its usage limit; "
            "retry with full history and without previous_response_id.",
        ),
        (
            "/v1/responses",
            "upstream_unavailable",
            "Previous response owner account is unavailable; retry later.",
        ),
    ],
)
def test_responses_websocket_short_previous_response_usage_limit_requests_safe_reset_only_for_codex(
    endpoint,
    expected_code,
    expected_message,
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
    connect_models: list[str | None] = []
    captured_preferred_accounts: list[str | None] = []
    handled_error_codes: list[str] = []

    class _FakeSettingsCache:
        async def get(self):
            return _websocket_settings()

    async def allow_firewall(_websocket):
        return None

    async def allow_proxy_api_key(_authorization: str | None, *, request: object | None = None):
        return None

    async def fake_resolve_previous_response_owner(
        self,
        *,
        previous_response_id,
        api_key,
        session_id=None,
        surface,
    ):
        del self, previous_response_id, api_key, session_id, surface
        return "acct_ws_proxy_owner"

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
            api_key,
            client_send_lock,
            websocket,
        )
        connect_models.append(model)
        captured_preferred_accounts.append(request_state.preferred_account_id)
        return SimpleNamespace(id="acct_ws_proxy_owner"), first_upstream

    async def fake_handle_stream_error(self, account, error, code):
        del self, account, error
        handled_error_codes.append(code)

    async def fake_write_request_log(self, **kwargs):
        del self, kwargs

    monkeypatch.setattr(proxy_api_module, "_websocket_firewall_denial_response", allow_firewall)
    monkeypatch.setattr(proxy_api_module, "validate_proxy_api_key_authorization", allow_proxy_api_key)
    monkeypatch.setattr(proxy_module, "get_settings_cache", lambda: _FakeSettingsCache())
    monkeypatch.setattr(
        proxy_module.ProxyService,
        "_resolve_websocket_previous_response_owner",
        fake_resolve_previous_response_owner,
    )
    monkeypatch.setattr(proxy_module.ProxyService, "_connect_proxy_websocket", fake_connect_proxy_websocket)
    monkeypatch.setattr(proxy_module.ProxyService, "_handle_stream_error", fake_handle_stream_error)
    monkeypatch.setattr(proxy_module.ProxyService, "_write_request_log", fake_write_request_log)

    request_payload = {
        "type": "response.create",
        "model": "gpt-5.1",
        "instructions": "",
        "previous_response_id": "resp_ws_prev_anchor",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "continue"}]}],
        "stream": True,
    }

    with TestClient(app_instance) as client:
        with client.websocket_connect(endpoint) as websocket:
            websocket.send_text(json.dumps(request_payload))
            event = json.loads(websocket.receive_text())

    assert event["type"] == "response.failed"
    assert event["response"]["error"]["code"] == expected_code
    assert event["response"]["error"]["message"] == expected_message
    assert connect_models == ["gpt-5.1"]
    assert captured_preferred_accounts == ["acct_ws_proxy_owner"]
    assert handled_error_codes == ["usage_limit_reached"]


def test_backend_responses_websocket_transparent_replay_emits_no_accounts_when_reconnect_fails(
    app_instance,
    monkeypatch,
):
    first_upstream = _FakeUpstreamWebSocket(
        [
            _FakeUpstreamMessage(
                "text",
                text=json.dumps(
                    {
                        "type": "response.failed",
                        "response": {
                            "id": "resp_ws_quota_fail_no_accounts",
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
        )
        connect_models.append(model)
        if len(connect_models) == 1:
            del client_send_lock, websocket
            return SimpleNamespace(id="acct_ws_proxy_1"), first_upstream
        async with client_send_lock:
            await websocket.send_text(
                json.dumps(
                    {
                        "type": "error",
                        "status": 503,
                        "error": {"code": "no_accounts", "message": "No active accounts available"},
                    }
                )
            )
        return None, None

    async def fake_handle_stream_error(self, account, error, code):
        del self, account, error
        handled_error_codes.append(code)

    monkeypatch.setattr(proxy_api_module, "_websocket_firewall_denial_response", allow_firewall)
    monkeypatch.setattr(proxy_api_module, "validate_proxy_api_key_authorization", allow_proxy_api_key)
    monkeypatch.setattr(proxy_module, "get_settings_cache", lambda: _FakeSettingsCache())
    monkeypatch.setattr(proxy_module.ProxyService, "_connect_proxy_websocket", fake_connect_proxy_websocket)
    monkeypatch.setattr(proxy_module.ProxyService, "_handle_stream_error", fake_handle_stream_error)

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
            event = json.loads(websocket.receive_text())

    assert event["type"] == "error"
    assert event["status"] == 503
    assert event["error"]["code"] == "no_accounts"
    assert connect_models == ["gpt-5.1", "gpt-5.1"]
    assert handled_error_codes == ["usage_limit_reached"]
    assert first_upstream.closed is True


def test_backend_responses_websocket_emits_no_accounts_error(app_instance, monkeypatch):
    request_payload = {
        "type": "response.create",
        "model": "gpt-5.4",
        "instructions": "",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}],
        "stream": True,
    }

    async def allow_firewall(_websocket):
        return None

    async def allow_proxy_api_key(authorization: str | None, *, request: object | None = None):
        assert authorization is None
        return None

    class _FakeSettingsCache:
        async def get(self):
            return _websocket_settings()

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
            self,
        )
        async with client_send_lock:
            await websocket.send_text(json.dumps({"type": "error", "status": 503, "error": {"code": "no_accounts"}}))
        return None, None

    monkeypatch.setattr(proxy_api_module, "_websocket_firewall_denial_response", allow_firewall)
    monkeypatch.setattr(proxy_api_module, "validate_proxy_api_key_authorization", allow_proxy_api_key)
    monkeypatch.setattr(proxy_module, "get_settings_cache", lambda: _FakeSettingsCache())
    monkeypatch.setattr(proxy_module.ProxyService, "_connect_proxy_websocket", fake_connect_proxy_websocket)

    with TestClient(app_instance) as client:
        with client.websocket_connect("/backend-api/codex/responses") as websocket:
            websocket.send_text(json.dumps(request_payload))
            event = json.loads(websocket.receive_text())

    assert event["type"] == "error"
    assert event["status"] == 503
    assert event["error"]["code"] == "no_accounts"
