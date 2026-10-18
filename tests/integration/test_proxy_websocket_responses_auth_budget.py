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


def test_backend_responses_websocket_generic_auth_refresh_budget_is_per_account(
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
    account_a_first = _SequencedUpstreamWebSocket([], deferred_message_batches=[auth_failure_batch])
    account_a_refreshed = _SequencedUpstreamWebSocket([], deferred_message_batches=[auth_failure_batch])
    account_b_first = _SequencedUpstreamWebSocket([], deferred_message_batches=[auth_failure_batch])
    account_b_refreshed = _SequencedUpstreamWebSocket(
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
        if "acct_ws_auth_a" not in excluded:
            connect_accounts.append("acct_ws_auth_a")
            if len([account for account in connect_accounts if account == "acct_ws_auth_a"]) == 1:
                return SimpleNamespace(id="acct_ws_auth_a"), account_a_first
            return SimpleNamespace(id="acct_ws_auth_a"), account_a_refreshed
        connect_accounts.append("acct_ws_auth_b")
        if getattr(request_state, "force_refresh_account_id", None) == "acct_ws_auth_b":
            return SimpleNamespace(id="acct_ws_auth_b"), account_b_refreshed
        return SimpleNamespace(id="acct_ws_auth_b"), account_b_first

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
    assert connect_accounts == [
        "acct_ws_auth_a",
        "acct_ws_auth_a",
        "acct_ws_auth_b",
        "acct_ws_auth_b",
    ]
    assert forced_refresh_markers == [None, "acct_ws_auth_a", None, "acct_ws_auth_b"]
    assert permanent_failures == [("acct_ws_auth_a", "account_auth_invalidated")]
