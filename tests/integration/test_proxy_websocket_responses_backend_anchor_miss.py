from __future__ import annotations
import asyncio
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


def test_backend_responses_websocket_connect_failure_masks_previous_response_not_found(
    app_instance,
    monkeypatch,
):
    class _FakeSettingsCache:
        async def get(self):
            return _websocket_settings()

    async def allow_firewall(_websocket):
        return None

    async def allow_proxy_api_key(_authorization: str | None, *, request: object | None = None):
        return None

    async def fake_select_websocket_connect_account(
        self,
        deadline,
        *,
        sticky_key,
        sticky_kind,
        prefer_earlier_reset,
        prefer_earlier_reset_window,
        routing_strategy,
        model,
        request_state,
        api_key,
        client_send_lock,
        websocket,
        reallocate_sticky,
        sticky_max_age_seconds,
        exclude_account_ids,
        preferred_account_id,
        require_security_work_authorized,
        require_preferred_account,
        defer_no_account_error,
    ):
        del (
            self,
            deadline,
            sticky_key,
            sticky_kind,
            prefer_earlier_reset,
            prefer_earlier_reset_window,
            routing_strategy,
            model,
            api_key,
            client_send_lock,
            websocket,
            reallocate_sticky,
            sticky_max_age_seconds,
            exclude_account_ids,
            preferred_account_id,
            require_security_work_authorized,
            require_preferred_account,
            defer_no_account_error,
        )
        assert request_state.previous_response_id == "resp_ws_prev_anchor"
        return SimpleNamespace(id="acct_ws_prev_connect_failure")

    async def fake_try_open_websocket_connect_attempt(
        self,
        account,
        headers,
        *,
        deadline,
        api_key,
        request_state,
        client_send_lock,
        websocket,
        force_refresh,
    ):
        del self, account, headers, deadline, api_key, request_state, client_send_lock, websocket, force_refresh
        payload = proxy_module.openai_error(
            "previous_response_not_found",
            "Previous response with id 'resp_ws_prev_anchor' not found.",
            error_type="invalid_request_error",
        )
        payload["error"]["param"] = "previous_response_id"
        raise proxy_module.ProxyResponseError(400, payload)

    monkeypatch.setattr(proxy_api_module, "_websocket_firewall_denial_response", allow_firewall)
    monkeypatch.setattr(proxy_api_module, "validate_proxy_api_key_authorization", allow_proxy_api_key)
    monkeypatch.setattr(proxy_module, "get_settings_cache", lambda: _FakeSettingsCache())
    monkeypatch.setattr(
        proxy_module.ProxyService,
        "_select_websocket_connect_account",
        fake_select_websocket_connect_account,
    )
    monkeypatch.setattr(
        proxy_module.ProxyService,
        "_try_open_websocket_connect_attempt",
        fake_try_open_websocket_connect_attempt,
    )
    monkeypatch.setattr(
        proxy_module.ProxyService,
        "_decide_websocket_failover_action",
        lambda *args, **kwargs: asyncio.sleep(0, result="surface"),
    )
    monkeypatch.setattr(
        proxy_module.ProxyService,
        "_release_websocket_reservation",
        lambda *args, **kwargs: asyncio.sleep(0),
    )

    request_payload = {
        "type": "response.create",
        "model": "gpt-5.1",
        "instructions": "",
        "previous_response_id": "resp_ws_prev_anchor",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "continue"}]}],
        "stream": True,
    }

    with TestClient(app_instance) as client:
        with client.websocket_connect("/backend-api/codex/responses") as websocket:
            websocket.send_text(json.dumps(request_payload))
            event = json.loads(websocket.receive_text())

    assert event["type"] == "error"
    assert event["status"] == 502
    _assert_codex_previous_response_stale_error(event["error"])


def test_backend_responses_websocket_masks_short_previous_response_not_found_without_retry(
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
                                "message": "Previous response with id 'resp_ws_prev_anchor' not found.",
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
    captured_preferred_accounts: list[str | None] = []

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
        captured_preferred_accounts.append(request_state.preferred_account_id)
        if connect_count == 1:
            return SimpleNamespace(id="acct_ws_prev_mask"), first_upstream
        return SimpleNamespace(id="acct_ws_prev_mask"), recovered_upstream

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
        return "acct_ws_prev_mask"

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
    _assert_codex_previous_response_stale_error(failed_2["response"]["error"])
    assert "previous_response_not_found" not in json.dumps(failed_2)
    assert "resp_ws_prev_anchor" not in json.dumps(failed_2)
    assert connect_count == 1
    assert captured_preferred_accounts == [None]


def test_backend_responses_websocket_masks_anonymous_previous_response_not_found_with_inflight_request(
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
                            "type": "response.created",
                            "response": {"id": "resp_ws_inflight", "status": "in_progress"},
                        },
                        separators=(",", ":"),
                    ),
                )
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
                                "message": "Previous response with id 'resp_ws_prev_anchor' not found.",
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
                            "response": {
                                "id": "resp_ws_inflight",
                                "status": "completed",
                                "usage": {"input_tokens": 2, "output_tokens": 1, "total_tokens": 3},
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
        return SimpleNamespace(id="acct_ws_prev_followup"), fake_upstream

    async def fake_write_request_log(self, **kwargs):
        del self
        log_calls.append(kwargs)

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
    monkeypatch.setattr(proxy_module.ProxyService, "_write_request_log", fake_write_request_log)
    monkeypatch.setattr(
        proxy_module.ProxyService,
        "_resolve_websocket_previous_response_owner",
        fake_resolve_previous_response_owner,
    )

    first_request = {
        "type": "response.create",
        "model": "gpt-5.4",
        "instructions": "",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "first"}]}],
        "stream": True,
    }
    followup_request = {
        "type": "response.create",
        "model": "gpt-5.4",
        "instructions": "",
        "previous_response_id": "resp_ws_prev_anchor",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "continue"}]}],
        "stream": True,
    }

    with TestClient(app_instance) as client:
        with client.websocket_connect(
            "/backend-api/codex/responses",
            headers={"Authorization": "Bearer external-token"},
        ) as websocket:
            websocket.send_text(json.dumps(first_request))
            created_event = json.loads(websocket.receive_text())

            websocket.send_text(json.dumps(followup_request))
            failed_event = json.loads(websocket.receive_text())
            completed_event = json.loads(websocket.receive_text())

    assert created_event["type"] == "response.created"
    assert created_event["response"]["id"] == "resp_ws_inflight"
    assert failed_event["type"] == "response.failed"
    _assert_codex_previous_response_stale_error(failed_event["response"]["error"])
    assert "previous_response_not_found" not in json.dumps(failed_event)
    assert completed_event["type"] == "response.completed"
    assert completed_event["response"]["id"] == "resp_ws_inflight"
    assert any(
        call["status"] == "error" and call["error_code"] == proxy_module.PREVIOUS_RESPONSE_STALE_CODE
        for call in log_calls
    )
    assert any(call["status"] == "success" and call["request_id"] == "resp_ws_inflight" for call in log_calls)
    assert fake_upstream.closed is True
