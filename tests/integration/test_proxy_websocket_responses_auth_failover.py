from __future__ import annotations
import json
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketDenialResponse
from starlette.websockets import WebSocketDisconnect
import app.core.auth.dependencies as auth_dependencies
import app.modules.proxy.api as proxy_api_module
import app.modules.proxy.service as proxy_module
from app.core.exceptions import ProxyAuthError
from tests.integration.proxy_websocket_responses_support import (
    _FakeUpstreamMessage,
    _SequencedUpstreamWebSocket,
    _stub_request_logging,
    _websocket_settings,
)

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("endpoint", ["/backend-api/codex/responses", "/v1/responses"])
def test_chatgpt_bearer_websocket_member_header_is_checked_before_accept(app_instance, monkeypatch, endpoint):
    async def allow_firewall(_websocket):
        return None

    async def proxy_settings():
        return SimpleNamespace(api_key_auth_enabled=False, team_mode_enabled=True)

    async def validate_member(token):
        if token in {"sk-clb-invalid", "sk-clb-revoked", "sk-clb-expired"}:
            raise ProxyAuthError("Invalid API key")
        assert token in {"sk-clb-member-key", "sk-clb-bearer-key"}
        return SimpleNamespace(id=token, member_id="member-1")

    validated: list[str | None] = []

    class RecordingService:
        async def proxy_responses_websocket(self, websocket, headers, *, api_key, **_kwargs):
            validated.append(api_key.id if api_key is not None else None)
            await websocket.close()

    monkeypatch.setattr(proxy_api_module, "_websocket_firewall_denial_response", allow_firewall)
    monkeypatch.setattr(auth_dependencies, "get_settings_cache", lambda: SimpleNamespace(get=proxy_settings))
    monkeypatch.setattr(auth_dependencies, "is_local_request", lambda _request: False)
    monkeypatch.setattr(auth_dependencies, "_is_proxy_unauthenticated_client_allowed", lambda _request: False)
    monkeypatch.setattr(auth_dependencies, "_validate_api_key_token", validate_member)
    monkeypatch.setattr(
        proxy_module.ProxyService, "proxy_responses_websocket", RecordingService.proxy_responses_websocket
    )

    headers = {"Authorization": "Bearer chatgpt-oauth"}
    with TestClient(app_instance) as client:
        with pytest.raises(WebSocketDenialResponse) as exc_info:
            with client.websocket_connect(endpoint, headers=headers):
                pytest.fail("unauthenticated websocket was accepted")
        assert exc_info.value.status_code == 401

        with client.websocket_connect(endpoint, headers={**headers, "x-api-key": " sk-clb-member-key "}) as websocket:
            with pytest.raises(WebSocketDisconnect):
                websocket.receive_text()
        assert validated == ["sk-clb-member-key"]

        with client.websocket_connect(
            endpoint, headers={"Authorization": "Bearer sk-clb-bearer-key", "x-api-key": "sk-clb-member-key"}
        ) as websocket:
            with pytest.raises(WebSocketDisconnect):
                websocket.receive_text()
        assert validated[-1] == "sk-clb-bearer-key"

        for member_key in ("sk-clb-invalid", "sk-clb-revoked", "sk-clb-expired"):
            with pytest.raises(WebSocketDenialResponse) as exc_info:
                with client.websocket_connect(endpoint, headers={**headers, "x-api-key": member_key}):
                    pytest.fail("invalid member key was accepted")
            assert exc_info.value.status_code == 401

        monkeypatch.setattr(auth_dependencies, "is_local_request", lambda _request: True)
        with client.websocket_connect(endpoint, headers=headers) as websocket:
            with pytest.raises(WebSocketDisconnect):
                websocket.receive_text()
        assert validated[-1] is None
        with pytest.raises(WebSocketDenialResponse) as exc_info:
            with client.websocket_connect(endpoint, headers={**headers, "x-api-key": "sk-clb-revoked"}):
                pytest.fail("revoked member key got keyless access")
        assert exc_info.value.status_code == 401


def test_backend_responses_websocket_session_ended_auth_failure_fails_over_before_visible_output(
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
                            "type": "response.failed",
                            "response": {
                                "id": "resp_ws_session_expired",
                                "status": "failed",
                                "error": {
                                    "type": "authentication_error",
                                    "code": "invalid_api_key",
                                    "message": "Your session has ended. Please log in again.",
                                },
                            },
                        },
                        separators=(",", ":"),
                    ),
                )
            ]
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
                            "response": {"id": "resp_ws_session_recovered", "status": "in_progress"},
                        },
                        separators=(",", ":"),
                    ),
                ),
                _FakeUpstreamMessage(
                    "text",
                    text=json.dumps(
                        {
                            "type": "response.completed",
                            "response": {"id": "resp_ws_session_recovered", "status": "completed"},
                        },
                        separators=(",", ":"),
                    ),
                ),
            ]
        ],
    )
    connect_accounts: list[str] = []
    permanent_failures: list[tuple[str, str]] = []

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
        excluded = getattr(request_state, "excluded_account_ids", set())
        if "acct_ws_expired" in excluded:
            connect_accounts.append("acct_ws_recovered")
            return SimpleNamespace(id="acct_ws_recovered"), recovered_upstream
        connect_accounts.append("acct_ws_expired")
        return SimpleNamespace(id="acct_ws_expired"), first_upstream

    async def fake_mark_permanent_failure(self, account, error_code):
        del self
        permanent_failures.append((account.id, error_code))

    monkeypatch.setattr(proxy_api_module, "_websocket_firewall_denial_response", allow_firewall)
    monkeypatch.setattr(proxy_api_module, "validate_proxy_api_key_authorization", allow_proxy_api_key)
    monkeypatch.setattr(proxy_module, "get_settings_cache", lambda: _FakeSettingsCache())
    monkeypatch.setattr(proxy_module.ProxyService, "_connect_proxy_websocket", fake_connect_proxy_websocket)
    monkeypatch.setattr(proxy_module.LoadBalancer, "mark_permanent_failure", fake_mark_permanent_failure)

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
            created = json.loads(websocket.receive_text())
            completed = json.loads(websocket.receive_text())

    assert created["type"] == "response.created"
    assert completed["type"] == "response.completed"
    assert completed["response"]["id"] == "resp_ws_session_recovered"
    assert connect_accounts == ["acct_ws_expired", "acct_ws_recovered"]
    assert permanent_failures == [("acct_ws_expired", "account_session_expired")]


def test_backend_responses_websocket_generic_auth_failure_refreshes_once_then_fails_over(
    app_instance,
    monkeypatch,
):
    auth_failure_batch = [
        _FakeUpstreamMessage(
            "text",
            text=json.dumps(
                {
                    "type": "response.failed",
                    "response": {
                        "id": "resp_ws_auth_failed",
                        "status": "failed",
                        "error": {
                            "type": "authentication_error",
                            "code": "invalid_api_key",
                            "message": "token invalidated",
                        },
                    },
                },
                separators=(",", ":"),
            ),
        )
    ]
    first_upstream = _SequencedUpstreamWebSocket([], deferred_message_batches=[auth_failure_batch])
    refreshed_upstream = _SequencedUpstreamWebSocket([], deferred_message_batches=[auth_failure_batch])
    recovered_upstream = _SequencedUpstreamWebSocket(
        [],
        deferred_message_batches=[
            [
                _FakeUpstreamMessage(
                    "text",
                    text=json.dumps(
                        {
                            "type": "response.created",
                            "response": {"id": "resp_ws_auth_recovered", "status": "in_progress"},
                        },
                        separators=(",", ":"),
                    ),
                ),
                _FakeUpstreamMessage(
                    "text",
                    text=json.dumps(
                        {
                            "type": "response.completed",
                            "response": {"id": "resp_ws_auth_recovered", "status": "completed"},
                        },
                        separators=(",", ":"),
                    ),
                ),
            ]
        ],
    )
    connect_accounts: list[str] = []
    forced_refresh_markers: list[str | None] = []
    permanent_failures: list[tuple[str, str]] = []

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
        forced_refresh_markers.append(getattr(request_state, "force_refresh_account_id", None))
        excluded = getattr(request_state, "excluded_account_ids", set())
        if "acct_ws_auth" in excluded:
            connect_accounts.append("acct_ws_auth_recovered")
            return SimpleNamespace(id="acct_ws_auth_recovered"), recovered_upstream
        connect_accounts.append("acct_ws_auth")
        if len(connect_accounts) == 1:
            return SimpleNamespace(id="acct_ws_auth"), first_upstream
        return SimpleNamespace(id="acct_ws_auth"), refreshed_upstream

    async def fake_mark_permanent_failure(self, account, error_code):
        del self
        permanent_failures.append((account.id, error_code))

    monkeypatch.setattr(proxy_api_module, "_websocket_firewall_denial_response", allow_firewall)
    monkeypatch.setattr(proxy_api_module, "validate_proxy_api_key_authorization", allow_proxy_api_key)
    monkeypatch.setattr(proxy_module, "get_settings_cache", lambda: _FakeSettingsCache())
    monkeypatch.setattr(proxy_module.ProxyService, "_connect_proxy_websocket", fake_connect_proxy_websocket)
    monkeypatch.setattr(proxy_module.LoadBalancer, "mark_permanent_failure", fake_mark_permanent_failure)

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
            created = json.loads(websocket.receive_text())
            completed = json.loads(websocket.receive_text())

    assert created["type"] == "response.created"
    assert completed["type"] == "response.completed"
    assert completed["response"]["id"] == "resp_ws_auth_recovered"
    assert connect_accounts == ["acct_ws_auth", "acct_ws_auth", "acct_ws_auth_recovered"]
    assert forced_refresh_markers[1] == "acct_ws_auth"
    assert permanent_failures == [("acct_ws_auth", "account_auth_invalidated")]
