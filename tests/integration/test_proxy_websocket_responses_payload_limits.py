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


def test_backend_responses_websocket_rejects_oversized_response_create_before_upstream(
    app_instance,
    monkeypatch,
    tmp_path,
):
    class _FakeSettingsCache:
        async def get(self):
            return _websocket_settings()

    async def allow_firewall(_websocket):
        return None

    async def allow_proxy_api_key(_authorization: str | None, *, request: object | None = None):
        return None

    async def fail_connect_proxy_websocket(
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
        raise AssertionError("oversized response.create must fail before upstream websocket connect")

    monkeypatch.setattr(proxy_api_module, "_websocket_firewall_denial_response", allow_firewall)
    monkeypatch.setattr(proxy_api_module, "validate_proxy_api_key_authorization", allow_proxy_api_key)
    monkeypatch.setattr(proxy_module, "get_settings_cache", lambda: _FakeSettingsCache())
    monkeypatch.setattr(proxy_module, "_UPSTREAM_RESPONSE_CREATE_WARN_BYTES", 64)
    monkeypatch.setattr(proxy_module, "_UPSTREAM_RESPONSE_CREATE_MAX_BYTES", 128)
    monkeypatch.setattr(proxy_module, "_OVERSIZED_RESPONSE_CREATE_DUMP_DIR", tmp_path)
    monkeypatch.setattr(proxy_module.ProxyService, "_connect_proxy_websocket", fail_connect_proxy_websocket)

    request_payload = {
        "type": "response.create",
        "model": "gpt-5.4",
        "instructions": "",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "x" * 256}]}],
        "stream": True,
    }

    with TestClient(app_instance) as client:
        with client.websocket_connect("/backend-api/codex/responses") as websocket:
            websocket.send_text(json.dumps(request_payload))
            error_event = json.loads(websocket.receive_text())

    assert error_event["type"] == "error"
    assert error_event["status"] == 413
    assert error_event["error"]["code"] == "payload_too_large"
    assert error_event["error"]["type"] == "invalid_request_error"
    assert error_event["error"]["param"] == "input"
    assert "response.create is too large for upstream websocket" in error_event["error"]["message"]

    meta_files = list(tmp_path.glob("*.meta.json"))
    assert len(meta_files) == 1
    meta = json.loads(meta_files[0].read_text(encoding="utf-8"))
    assert meta["reason"]["error_code"] == "payload_too_large"
    assert meta["request"]["transport"] == "websocket"
    assert meta["request"]["request_text_bytes"] > 128


def test_backend_responses_websocket_slims_historical_inline_artifacts_and_succeeds(
    app_instance,
    monkeypatch,
):
    fake_upstream = _FakeUpstreamWebSocket(
        [
            _FakeUpstreamMessage(
                "text",
                text=json.dumps(
                    {
                        "type": "response.created",
                        "response": {"id": "resp_ws_slim", "object": "response", "status": "in_progress"},
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
                            "id": "resp_ws_slim",
                            "object": "response",
                            "status": "completed",
                            "usage": {"input_tokens": 3, "output_tokens": 1, "total_tokens": 4},
                        },
                    },
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
    monkeypatch.setattr(proxy_module, "_UPSTREAM_RESPONSE_CREATE_WARN_BYTES", 64)
    monkeypatch.setattr(proxy_module, "_UPSTREAM_RESPONSE_CREATE_MAX_BYTES", 512)
    monkeypatch.setattr(proxy_module.ProxyService, "_connect_proxy_websocket", fake_connect_proxy_websocket)

    request_payload = {
        "type": "response.create",
        "model": "gpt-5.4",
        "instructions": "",
        "input": [
            {"role": "user", "content": [{"type": "input_text", "text": "old turn"}]},
            {
                "type": "function_call_output",
                "call_id": "call_1",
                "output": "data:image/png;base64," + ("A" * 1500),
            },
            {"role": "assistant", "content": [{"type": "output_text", "text": "done"}]},
            {"role": "user", "content": [{"type": "input_text", "text": "ping"}]},
        ],
        "stream": True,
    }

    with TestClient(app_instance) as client:
        with client.websocket_connect("/backend-api/codex/responses") as websocket:
            websocket.send_text(json.dumps(request_payload))
            created_event = json.loads(websocket.receive_text())
            completed_event = json.loads(websocket.receive_text())

    assert created_event["type"] == "response.created"
    assert completed_event["type"] == "response.completed"
    sent_payload = json.loads(fake_upstream.sent_text[0])
    assert sent_payload["input"][-1]["content"][0]["text"] == "ping"
    assert "data:image/" not in json.dumps(sent_payload["input"], ensure_ascii=True)
    assert "historical tool output" in json.dumps(sent_payload["input"], ensure_ascii=True)
