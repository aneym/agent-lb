from __future__ import annotations
import json
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
import app.modules.proxy.api as proxy_api_module
import app.modules.proxy.service as proxy_module
from tests.integration.proxy_websocket_responses_support import (
    _FakeUpstreamMessage,
    _SequencedUpstreamWebSocket,
    _assert_codex_previous_response_stale_error,
    _stub_request_logging,
    _websocket_settings,
)

pytestmark = pytest.mark.integration


def test_backend_responses_websocket_matches_previous_response_error_to_anchor_with_two_followups(
    app_instance,
    monkeypatch,
):
    first_upstream = _SequencedUpstreamWebSocket(
        [],
        deferred_message_batches=[
            [
                _FakeUpstreamMessage(
                    "text",
                    text=json.dumps(
                        {
                            "type": "response.created",
                            "response": {"id": "resp_ws_prev_anchor_a", "status": "in_progress"},
                        },
                        separators=(",", ":"),
                    ),
                ),
                _FakeUpstreamMessage(
                    "text",
                    text=json.dumps(
                        {
                            "type": "response.completed",
                            "response": {"id": "resp_ws_prev_anchor_a", "status": "completed"},
                        },
                        separators=(",", ":"),
                    ),
                ),
            ],
            [
                _FakeUpstreamMessage(
                    "text",
                    text=json.dumps(
                        {
                            "type": "response.created",
                            "response": {"id": "resp_ws_prev_anchor_b", "status": "in_progress"},
                        },
                        separators=(",", ":"),
                    ),
                ),
                _FakeUpstreamMessage(
                    "text",
                    text=json.dumps(
                        {
                            "type": "response.completed",
                            "response": {"id": "resp_ws_prev_anchor_b", "status": "completed"},
                        },
                        separators=(",", ":"),
                    ),
                ),
            ],
            [
                _FakeUpstreamMessage(
                    "text",
                    text=json.dumps(
                        {
                            "type": "response.created",
                            "response": {"id": "resp_ws_followup_a", "status": "in_progress"},
                        },
                        separators=(",", ":"),
                    ),
                ),
            ],
            [
                _FakeUpstreamMessage(
                    "text",
                    text=json.dumps(
                        {
                            "type": "response.created",
                            "response": {"id": "resp_ws_followup_b", "status": "in_progress"},
                        },
                        separators=(",", ":"),
                    ),
                ),
                _FakeUpstreamMessage(
                    "text",
                    text=json.dumps(
                        {
                            "type": "error",
                            "status": 400,
                            "error": {
                                "type": "invalid_request_error",
                                "code": "previous_response_not_found",
                                "message": "Cannot continue conversation because upstream lost resp_ws_prev_anchor_a.",
                                "param": "previous_response_id",
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
                            "response": {"id": "resp_ws_followup_b", "status": "completed"},
                        },
                        separators=(",", ":"),
                    ),
                ),
            ],
        ],
    )
    recovered_upstream = _SequencedUpstreamWebSocket(
        [],
        deferred_message_batches=[
            [
                _FakeUpstreamMessage(
                    "text",
                    text=json.dumps(
                        {
                            "type": "response.created",
                            "response": {"id": "resp_ws_after_error", "status": "in_progress"},
                        },
                        separators=(",", ":"),
                    ),
                ),
                _FakeUpstreamMessage(
                    "text",
                    text=json.dumps(
                        {
                            "type": "response.completed",
                            "response": {"id": "resp_ws_after_error", "status": "completed"},
                        },
                        separators=(",", ":"),
                    ),
                ),
            ]
        ],
    )
    connect_count = 0

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
        nonlocal connect_count
        connect_count += 1
        if connect_count == 1:
            return SimpleNamespace(id="acct_ws_followup_prev_nf"), first_upstream
        return SimpleNamespace(id="acct_ws_followup_prev_nf"), recovered_upstream

    async def fake_resolve_previous_response_owner(
        self,
        *,
        previous_response_id,
        api_key,
        session_id=None,
        surface,
    ):
        del self, api_key, session_id, surface
        assert previous_response_id in {"resp_ws_prev_anchor_a", "resp_ws_prev_anchor_b"}
        return "acct_ws_followup_prev_nf"

    monkeypatch.setattr(proxy_api_module, "_websocket_firewall_denial_response", allow_firewall)
    monkeypatch.setattr(proxy_api_module, "validate_proxy_api_key_authorization", allow_proxy_api_key)
    monkeypatch.setattr(proxy_module, "get_settings_cache", lambda: _FakeSettingsCache())
    monkeypatch.setattr(proxy_module.ProxyService, "_connect_proxy_websocket", fake_connect_proxy_websocket)
    monkeypatch.setattr(
        proxy_module.ProxyService,
        "_resolve_websocket_previous_response_owner",
        fake_resolve_previous_response_owner,
    )

    with TestClient(app_instance) as client:
        with client.websocket_connect("/backend-api/codex/responses") as websocket:
            websocket.send_text(json.dumps({"type": "response.create", "model": "gpt-5.4", "input": "anchor-a"}))
            created_1 = json.loads(websocket.receive_text())
            completed_1 = json.loads(websocket.receive_text())

            websocket.send_text(json.dumps({"type": "response.create", "model": "gpt-5.4", "input": "anchor-b"}))
            created_2 = json.loads(websocket.receive_text())
            completed_2 = json.loads(websocket.receive_text())

            websocket.send_text(
                json.dumps(
                    {
                        "type": "response.create",
                        "model": "gpt-5.4",
                        "input": "continue-a",
                        "previous_response_id": "resp_ws_prev_anchor_a",
                        "stream": True,
                    }
                )
            )
            created_3 = json.loads(websocket.receive_text())

            websocket.send_text(
                json.dumps(
                    {
                        "type": "response.create",
                        "model": "gpt-5.4",
                        "input": "continue-b",
                        "previous_response_id": "resp_ws_prev_anchor_b",
                        "stream": True,
                    }
                )
            )
            created_4 = json.loads(websocket.receive_text())
            failed_3 = json.loads(websocket.receive_text())
            completed_4 = json.loads(websocket.receive_text())

            websocket.send_text(json.dumps({"type": "response.create", "model": "gpt-5.4", "input": "after-error"}))
            created_5 = json.loads(websocket.receive_text())
            completed_5 = json.loads(websocket.receive_text())

    assert created_1["response"]["id"] == "resp_ws_prev_anchor_a"
    assert completed_1["response"]["id"] == "resp_ws_prev_anchor_a"
    assert created_2["response"]["id"] == "resp_ws_prev_anchor_b"
    assert completed_2["response"]["id"] == "resp_ws_prev_anchor_b"
    assert created_3["response"]["id"] == "resp_ws_followup_a"
    assert created_4["response"]["id"] == "resp_ws_followup_b"
    assert failed_3["type"] == "response.failed"
    assert failed_3["response"]["id"] == "resp_ws_followup_a"
    _assert_codex_previous_response_stale_error(failed_3["response"]["error"])
    assert "previous_response_not_found" not in json.dumps(failed_3)
    assert completed_4["type"] == "response.completed"
    assert completed_4["response"]["id"] == "resp_ws_followup_b"
    assert created_5["response"]["id"] == "resp_ws_after_error"
    assert completed_5["response"]["id"] == "resp_ws_after_error"
    assert connect_count == 2
    assert first_upstream.closed is True
