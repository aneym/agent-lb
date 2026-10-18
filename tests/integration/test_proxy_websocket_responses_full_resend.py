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
    _stub_request_logging,
    _websocket_settings,
)

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    "upstream_error",
    [
        {
            "type": "invalid_request_error",
            "code": "previous_response_not_found",
            "message": "Previous response with id 'resp_ws_prev_anchor' not found.",
            "param": "previous_response_id",
        },
        {"type": "invalid_request_error", "message": "Invalid `previous_response_id`."},
        {"type": "invalid_request_error", "code": "usage_limit_reached", "message": "Usage limit reached."},
    ],
)
@pytest.mark.parametrize("endpoint", ["/v1/responses", "/backend-api/codex/responses"])
def test_responses_websocket_replays_client_full_resend_previous_response_miss_without_anchor(
    endpoint,
    upstream_error,
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
                            "response": {"id": "resp_ws_prev_anchor", "status": "in_progress"},
                        },
                        separators=(",", ":"),
                    ),
                ),
                _FakeUpstreamMessage(
                    "text",
                    text=json.dumps(
                        {
                            "type": "response.completed",
                            "response": {"id": "resp_ws_prev_anchor", "status": "completed"},
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
                            "type": "error",
                            "status": 400,
                            "error": upstream_error,
                        },
                        separators=(",", ":"),
                    ),
                )
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
                            "response": {"id": "resp_ws_prev_retry", "status": "in_progress"},
                        },
                        separators=(",", ":"),
                    ),
                ),
                _FakeUpstreamMessage(
                    "text",
                    text=json.dumps(
                        {
                            "type": "response.completed",
                            "response": {"id": "resp_ws_prev_retry", "status": "completed"},
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
            api_key,
            client_send_lock,
            websocket,
        )
        nonlocal connect_count
        connect_count += 1
        if connect_count == 1:
            return SimpleNamespace(id="acct_ws_prev_mask"), first_upstream
        if upstream_error.get("code") == "usage_limit_reached":
            assert request_state.preferred_account_id is None
            assert "acct_ws_prev_mask" in request_state.excluded_account_ids
            return SimpleNamespace(id="acct_ws_rotated"), recovered_upstream
        return SimpleNamespace(id="acct_ws_prev_mask"), recovered_upstream

    handled_errors = []

    async def fake_handle_stream_error(self, account, error, code):
        handled_errors.append((account.id, code))

    monkeypatch.setattr(proxy_module.ProxyService, "_handle_stream_error", fake_handle_stream_error)
    monkeypatch.setattr(proxy_api_module, "_websocket_firewall_denial_response", allow_firewall)
    monkeypatch.setattr(proxy_api_module, "validate_proxy_api_key_authorization", allow_proxy_api_key)
    monkeypatch.setattr(proxy_module, "get_settings_cache", lambda: _FakeSettingsCache())
    monkeypatch.setattr(proxy_module.ProxyService, "_connect_proxy_websocket", fake_connect_proxy_websocket)

    full_resend_input = [
        {"role": "user", "content": [{"type": "input_text", "text": "first"}]},
        {"role": "assistant", "content": [{"type": "output_text", "text": "first response"}]},
        {"role": "user", "content": [{"type": "input_text", "text": "continue"}]},
    ]

    with TestClient(app_instance) as client:
        with client.websocket_connect(endpoint) as websocket:
            websocket.send_text(
                json.dumps(
                    {
                        "type": "response.create",
                        "model": "gpt-5.4",
                        "input": [full_resend_input[0]],
                        "stream": True,
                    }
                )
            )
            created_1 = json.loads(websocket.receive_text())
            completed_1 = json.loads(websocket.receive_text())
            assert created_1["type"] == "response.created"
            assert completed_1["type"] == "response.completed"

            websocket.send_text(
                json.dumps(
                    {
                        "type": "response.create",
                        "model": "gpt-5.4",
                        "input": full_resend_input,
                        "previous_response_id": "resp_ws_prev_anchor",
                        "stream": True,
                    }
                )
            )
            created_2 = json.loads(websocket.receive_text())
            completed_2 = json.loads(websocket.receive_text())

    assert created_2["type"] == "response.created"
    assert created_2["response"]["id"] == "resp_ws_prev_retry"
    assert completed_2["type"] == "response.completed"
    assert "previous_response_not_found" not in json.dumps(created_2)
    assert connect_count == 2
    assert json.loads(first_upstream.sent_text[-1])["previous_response_id"] == "resp_ws_prev_anchor"
    replay_payload = json.loads(recovered_upstream.sent_text[-1])
    assert "previous_response_id" not in replay_payload
    assert replay_payload["input"] == full_resend_input

    if upstream_error.get("code") == "usage_limit_reached":
        assert handled_errors == [("acct_ws_prev_mask", "usage_limit_reached")]
    else:
        assert handled_errors == []
