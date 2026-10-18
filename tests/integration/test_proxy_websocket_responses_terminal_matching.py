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
    _SequencedUpstreamWebSocket,
    _stub_request_logging,
    _websocket_settings,
)

pytestmark = pytest.mark.integration


def test_backend_responses_websocket_matches_terminal_events_by_response_id(app_instance, monkeypatch):
    from app.core.identity import RequestIdentity

    turn_identities = iter(
        (
            RequestIdentity("member-a", "member", "box-1", "local"),
            RequestIdentity("member-b", "member", "box-2", "tailnet"),
        )
    )
    monkeypatch.setattr(
        "app.modules.proxy._service.websocket.mixin.get_request_identity", lambda: next(turn_identities)
    )
    fake_upstream = _SequencedUpstreamWebSocket(
        [],
        deferred_message_batches=[
            [
                _FakeUpstreamMessage(
                    "text",
                    text=json.dumps(
                        {"type": "response.created", "response": {"id": "resp_ws_a", "status": "in_progress"}},
                        separators=(",", ":"),
                    ),
                )
            ],
            [
                _FakeUpstreamMessage(
                    "text",
                    text=json.dumps(
                        {"type": "response.created", "response": {"id": "resp_ws_b", "status": "in_progress"}},
                        separators=(",", ":"),
                    ),
                ),
                _FakeUpstreamMessage(
                    "text",
                    text=json.dumps(
                        {
                            "type": "response.completed",
                            "response": {
                                "id": "resp_ws_b",
                                "status": "completed",
                                "usage": {"input_tokens": 7, "output_tokens": 11, "total_tokens": 18},
                            },
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
                                "id": "resp_ws_a",
                                "status": "completed",
                                "usage": {"input_tokens": 3, "output_tokens": 5, "total_tokens": 8},
                            },
                        },
                        separators=(",", ":"),
                    ),
                ),
            ],
        ],
    )
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
            websocket.send_text(json.dumps(second_request))
            events = [json.loads(websocket.receive_text()) for _ in range(4)]

    assert [event["type"] for event in events] == [
        "response.created",
        "response.created",
        "response.completed",
        "response.completed",
    ]
    assert len(log_calls) == 2
    assert log_calls[0]["request_id"] == "resp_ws_b"
    assert log_calls[0]["identity"] == RequestIdentity("member-b", "member", "box-2", "tailnet")
    assert log_calls[0]["model"] == "gpt-5.2"
    assert log_calls[0]["input_tokens"] == 7
    assert log_calls[1]["request_id"] == "resp_ws_a"
    assert log_calls[1]["identity"] == RequestIdentity("member-a", "member", "box-1", "local")
    assert log_calls[1]["model"] == "gpt-5.1"
    assert log_calls[1]["input_tokens"] == 3


def test_backend_responses_websocket_emits_response_failed_before_close_on_upstream_eof(app_instance, monkeypatch):
    def upstream_created_then_eof(response_id: str) -> _FakeUpstreamWebSocket:
        return _FakeUpstreamWebSocket(
            [
                _FakeUpstreamMessage(
                    "text",
                    text=json.dumps(
                        {"type": "response.created", "response": {"id": response_id, "status": "in_progress"}},
                        separators=(",", ":"),
                    ),
                ),
                _FakeUpstreamMessage("close", close_code=1011),
            ]
        )

    upstreams = [
        upstream_created_then_eof("resp_ws_eof"),
        upstream_created_then_eof("resp_ws_eof_retry"),
    ]
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
        return SimpleNamespace(id="acct_ws_proxy"), upstreams.pop(0)

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
            failed_event = json.loads(websocket.receive_text())

    assert created_event["type"] == "response.created"
    assert failed_event["type"] == "response.failed"
    assert failed_event["response"]["id"] == "resp_ws_eof"
    assert failed_event["response"]["error"]["code"] == "stream_incomplete"
    assert "close_code=1011" in failed_event["response"]["error"]["message"]
    assert len(log_calls) == 1
    assert log_calls[0]["request_id"] == "resp_ws_eof_retry"
    assert log_calls[0]["status"] == "error"
    assert log_calls[0]["error_code"] == "stream_incomplete"
