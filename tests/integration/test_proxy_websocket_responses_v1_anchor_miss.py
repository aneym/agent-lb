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


def test_v1_responses_websocket_masks_short_previous_response_not_found_without_retry(
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
                            "error": {
                                "type": "invalid_request_error",
                                "code": "previous_response_not_found",
                                "message": ("Previous response with id 'resp_ws_prev_anchor' not found."),
                                "param": "previous_response_id",
                            },
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
                        {"type": "response.created", "response": {"id": "resp_ws_prev_retry", "status": "in_progress"}},
                        separators=(",", ":"),
                    ),
                ),
                _FakeUpstreamMessage(
                    "text",
                    text=json.dumps(
                        {"type": "response.completed", "response": {"id": "resp_ws_prev_retry", "status": "completed"}},
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
            return SimpleNamespace(id="acct_ws_prev_mask"), first_upstream
        return SimpleNamespace(id="acct_ws_prev_mask"), recovered_upstream

    monkeypatch.setattr(proxy_api_module, "_websocket_firewall_denial_response", allow_firewall)
    monkeypatch.setattr(proxy_api_module, "validate_proxy_api_key_authorization", allow_proxy_api_key)
    monkeypatch.setattr(proxy_module, "get_settings_cache", lambda: _FakeSettingsCache())
    monkeypatch.setattr(proxy_module.ProxyService, "_connect_proxy_websocket", fake_connect_proxy_websocket)

    with TestClient(app_instance) as client:
        with client.websocket_connect("/v1/responses") as websocket:
            websocket.send_text(
                json.dumps(
                    {
                        "type": "response.create",
                        "model": "gpt-5.4",
                        "input": "hello",
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
                        "input": "continue",
                        "previous_response_id": "resp_ws_prev_anchor",
                        "stream": True,
                    }
                )
            )
            failed_2 = json.loads(websocket.receive_text())

    assert failed_2["type"] == "response.failed"
    assert failed_2["response"]["error"]["code"] == "stream_incomplete"
    assert failed_2["response"]["error"]["message"] == "Upstream websocket closed before response.completed"
    assert "previous_response_not_found" not in json.dumps(failed_2)
    assert "resp_ws_prev_anchor" not in json.dumps(failed_2)
    assert connect_count == 1


def test_v1_responses_websocket_marks_fresh_turn_as_retry_safe_at_prep_time(
    app_instance,
    monkeypatch,
):

    upstream_socket = _SequencedUpstreamWebSocket(
        [
            _FakeUpstreamMessage(
                "text",
                text=json.dumps(
                    {
                        "type": "response.created",
                        "response": {
                            "id": "resp_ws_fresh_turn_ok",
                            "status": "in_progress",
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
                            "id": "resp_ws_fresh_turn_ok",
                            "status": "completed",
                        },
                    },
                    separators=(",", ":"),
                ),
            ),
        ],
    )
    connect_count = 0
    connect_record: list[dict[str, object]] = []

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

        connect_record.append(
            {
                "connect_count": connect_count,
                "fresh_upstream_request_is_retry_safe": request_state.fresh_upstream_request_is_retry_safe,
                "fresh_upstream_request_text_set": bool(request_state.fresh_upstream_request_text),
            }
        )
        return SimpleNamespace(id="acct_ws_fresh_turn"), upstream_socket

    monkeypatch.setattr(proxy_api_module, "_websocket_firewall_denial_response", allow_firewall)
    monkeypatch.setattr(proxy_api_module, "validate_proxy_api_key_authorization", allow_proxy_api_key)
    monkeypatch.setattr(proxy_module, "get_settings_cache", lambda: _FakeSettingsCache())
    monkeypatch.setattr(proxy_module.ProxyService, "_connect_proxy_websocket", fake_connect_proxy_websocket)

    with TestClient(app_instance) as client:
        with client.websocket_connect("/v1/responses") as websocket:
            websocket.send_text(
                json.dumps(
                    {
                        "type": "response.create",
                        "model": "gpt-5.4",
                        "input": "hello",
                        "stream": True,
                    }
                )
            )
            created = json.loads(websocket.receive_text())
            completed = json.loads(websocket.receive_text())

    assert created["type"] == "response.created"
    assert completed["type"] == "response.completed"

    assert len(connect_record) == 1
    assert connect_record[0]["fresh_upstream_request_is_retry_safe"] is True
    assert connect_record[0]["fresh_upstream_request_text_set"] is True


@pytest.mark.parametrize(
    "upstream_error",
    [
        {
            "type": "invalid_request_error",
            "code": "invalid_request_error",
            "message": "Previous response with id 'resp_ws_prev_anchor' not found.",
            "param": "previous_response_id",
        },
        {"type": "invalid_request_error", "message": "Invalid `previous_response_id`."},
    ],
)
def test_v1_responses_websocket_masks_invalid_request_previous_response_not_found_without_retry(
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
                        {"type": "response.created", "response": {"id": "resp_ws_prev_retry", "status": "in_progress"}},
                        separators=(",", ":"),
                    ),
                ),
                _FakeUpstreamMessage(
                    "text",
                    text=json.dumps(
                        {"type": "response.completed", "response": {"id": "resp_ws_prev_retry", "status": "completed"}},
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
            return SimpleNamespace(id="acct_ws_prev_mask"), first_upstream
        return SimpleNamespace(id="acct_ws_prev_mask"), recovered_upstream

    monkeypatch.setattr(proxy_api_module, "_websocket_firewall_denial_response", allow_firewall)
    monkeypatch.setattr(proxy_api_module, "validate_proxy_api_key_authorization", allow_proxy_api_key)
    monkeypatch.setattr(proxy_module, "get_settings_cache", lambda: _FakeSettingsCache())
    monkeypatch.setattr(proxy_module.ProxyService, "_connect_proxy_websocket", fake_connect_proxy_websocket)

    with TestClient(app_instance) as client:
        with client.websocket_connect("/v1/responses") as websocket:
            websocket.send_text(
                json.dumps(
                    {
                        "type": "response.create",
                        "model": "gpt-5.4",
                        "input": "hello",
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
                        "input": "continue",
                        "previous_response_id": "resp_ws_prev_anchor",
                        "stream": True,
                    }
                )
            )
            failed_2 = json.loads(websocket.receive_text())

    assert failed_2["type"] == "response.failed"
    assert failed_2["response"]["error"]["code"] == "stream_incomplete"
    assert failed_2["response"]["error"]["message"] == "Upstream websocket closed before response.completed"
    assert "previous_response_not_found" not in json.dumps(failed_2)
    assert "resp_ws_prev_anchor" not in json.dumps(failed_2)
    assert connect_count == 1
