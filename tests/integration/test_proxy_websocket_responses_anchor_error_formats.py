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


def test_backend_responses_websocket_masks_top_level_previous_response_not_found_from_chatgpt_backend(
    app_instance,
    monkeypatch,
):
    fake_upstream = _SequencedUpstreamWebSocket(
        [],
        deferred_message_batches=[
            [
                _FakeUpstreamMessage(
                    "text",
                    text=json.dumps(
                        {
                            "type": "error",
                            "status_code": 400,
                            "error_type": "invalid_request_error",
                            "code": "previous_response_not_found",
                            "message": "Previous response with id 'resp_chatgpt_prev_anchor' not found.",
                            "param": "previous_response_id",
                        },
                        separators=(",", ":"),
                    ),
                )
            ],
        ],
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
        return SimpleNamespace(id="acct_ws_chatgpt_prev_top_level"), fake_upstream

    async def fake_resolve_previous_response_owner(
        self,
        *,
        previous_response_id,
        api_key,
        session_id=None,
        surface,
    ):
        del self, previous_response_id, api_key, session_id, surface
        return None

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
        with client.websocket_connect(
            "/backend-api/codex/responses",
            headers={"Authorization": "Bearer external-token"},
        ) as websocket:
            websocket.send_text(
                json.dumps(
                    {
                        "type": "response.create",
                        "model": "gpt-5.4",
                        "instructions": "",
                        "previous_response_id": "resp_chatgpt_prev_anchor",
                        "input": [{"role": "user", "content": [{"type": "input_text", "text": "continue"}]}],
                        "stream": True,
                    }
                )
            )
            failed_event = json.loads(websocket.receive_text())

    assert failed_event["type"] == "response.failed"
    _assert_codex_previous_response_stale_error(failed_event["response"]["error"])
    serialized = json.dumps(failed_event)
    assert "previous_response_not_found" not in serialized
    assert "resp_chatgpt_prev_anchor" not in serialized


def test_backend_responses_websocket_masks_pretty_previous_response_not_found_from_chatgpt_backend(
    app_instance,
    monkeypatch,
):
    upstream_socket = _SequencedUpstreamWebSocket(
        [
            _FakeUpstreamMessage(
                "text",
                text=json.dumps(
                    {
                        "type": "error",
                        "error": {
                            "type": "invalid_request_error",
                            "code": "previous_response_not_found",
                            "message": "Previous response with id 'resp_chatgpt_pretty_prev_anchor' not found.",
                            "param": "previous_response_id",
                        },
                        "status": 400,
                    },
                    indent=2,
                ),
            )
        ]
    )

    class _FakeSettingsCache:
        async def get(self):
            return _websocket_settings()

    async def allow_firewall(_websocket):
        return None

    async def allow_proxy_api_key(_authorization: str | None, **_kwargs):
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
        return SimpleNamespace(id="acct_ws_pretty_prev_mask"), upstream_socket

    monkeypatch.setattr(proxy_api_module, "_websocket_firewall_denial_response", allow_firewall)
    monkeypatch.setattr(proxy_api_module, "validate_proxy_api_key_authorization", allow_proxy_api_key)
    monkeypatch.setattr(proxy_module, "get_settings_cache", lambda: _FakeSettingsCache())
    monkeypatch.setattr(proxy_module.ProxyService, "_connect_proxy_websocket", fake_connect_proxy_websocket)

    with TestClient(app_instance) as client:
        with client.websocket_connect("/backend-api/codex/responses") as websocket:
            websocket.send_text(
                json.dumps(
                    {
                        "type": "response.create",
                        "model": "gpt-5.4",
                        "input": "continue",
                        "previous_response_id": "resp_chatgpt_pretty_prev_anchor",
                        "stream": True,
                    }
                )
            )
            failed_event = json.loads(websocket.receive_text())

    assert failed_event["type"] == "response.failed"
    _assert_codex_previous_response_stale_error(failed_event["response"]["error"])
    serialized = json.dumps(failed_event)
    assert "previous_response_not_found" not in serialized
    assert "resp_chatgpt_pretty_prev_anchor" not in serialized


def test_backend_responses_websocket_masks_previous_response_not_found_when_message_omits_response_id(
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
                                "message": "Previous response not found.",
                                "param": "previous_response_id",
                            },
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
                            "response": {"id": "resp_ws_followup_replayed", "status": "in_progress"},
                        },
                        separators=(",", ":"),
                    ),
                ),
                _FakeUpstreamMessage(
                    "text",
                    text=json.dumps(
                        {
                            "type": "response.completed",
                            "response": {"id": "resp_ws_followup_replayed", "status": "completed"},
                        },
                        separators=(",", ":"),
                    ),
                ),
            ],
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
            return SimpleNamespace(id="acct_ws_prev_nf_omitted_id"), first_upstream
        return SimpleNamespace(id="acct_ws_prev_nf_omitted_id"), recovered_upstream

    async def fake_resolve_previous_response_owner(
        self,
        *,
        previous_response_id,
        api_key,
        session_id=None,
        surface,
    ):
        del self, api_key, session_id, surface
        assert previous_response_id == "resp_ws_prev_anchor"
        return "acct_ws_prev_nf_omitted_id"

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
            websocket.send_text(json.dumps({"type": "response.create", "model": "gpt-5.4", "input": "hello"}))
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
    _assert_codex_previous_response_stale_error(failed_2["response"]["error"])
    assert "previous_response_not_found" not in json.dumps(failed_2)
    assert "resp_ws_prev_anchor" not in json.dumps(failed_2)
    assert connect_count == 1


def test_backend_responses_websocket_never_exposes_raw_previous_response_not_found_to_client(
    app_instance,
    monkeypatch,
):
    upstream = _SequencedUpstreamWebSocket(
        [],
        deferred_message_batches=[
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
                                "message": "Previous response with id 'resp_live_anchor' not found.",
                                "param": "previous_response_id",
                            },
                        },
                        separators=(",", ":"),
                    ),
                )
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
        del request
        return None

    async def fake_resolve_previous_response_owner(
        self,
        *,
        previous_response_id,
        api_key,
        session_id=None,
        surface,
    ):
        del self, api_key, session_id, surface
        assert previous_response_id == "resp_live_anchor"
        return "acct_live_anchor"

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
        return SimpleNamespace(id="acct_live_anchor"), upstream

    monkeypatch.setattr(proxy_api_module, "_websocket_firewall_denial_response", allow_firewall)
    monkeypatch.setattr(proxy_api_module, "validate_proxy_api_key_authorization", allow_proxy_api_key)
    monkeypatch.setattr(proxy_module, "get_settings_cache", lambda: _FakeSettingsCache())
    monkeypatch.setattr(
        proxy_module.ProxyService,
        "_resolve_websocket_previous_response_owner",
        fake_resolve_previous_response_owner,
    )
    monkeypatch.setattr(proxy_module.ProxyService, "_connect_proxy_websocket", fake_connect_proxy_websocket)

    request_payload = {
        "type": "response.create",
        "model": "gpt-5.4",
        "previous_response_id": "resp_live_anchor",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "Continue."}]}],
        "stream": True,
    }

    with TestClient(app_instance) as client:
        with client.websocket_connect(
            "/backend-api/codex/responses",
            headers={
                "Authorization": "Bearer external-token",
                "session_id": "thread-live-leak",
                "openai-beta": "responses_websockets=2026-02-06",
            },
        ) as websocket:
            websocket.send_text(json.dumps(request_payload))
            event = json.loads(websocket.receive_text())

    serialized_event = json.dumps(event)
    assert event["type"] == "response.failed"
    _assert_codex_previous_response_stale_error(event["response"]["error"])
    assert "previous_response_not_found" not in serialized_event
    assert "resp_live_anchor" not in serialized_event
    assert connect_count == 1
