from __future__ import annotations

import base64
import json

import pytest

import app.modules.proxy.service as proxy_module
from app.core.config.settings import Settings
from app.db.models import DashboardSettings


def _encode_jwt(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return f"header.{body}.sig"


def _make_auth_json(account_id: str, email: str) -> dict:
    payload = {
        "email": email,
        "chatgpt_account_id": account_id,
        "https://api.openai.com/auth": {"chatgpt_plan_type": "plus"},
    }
    return {
        "tokens": {
            "idToken": _encode_jwt(payload),
            "accessToken": "access-token",
            "refreshToken": "refresh-token",
            "accountId": account_id,
        },
    }


async def _import_account(async_client, account_id: str, email: str) -> None:
    auth_json = _make_auth_json(account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200


def _sse(payload: dict[str, object]) -> str:
    return f"data: {json.dumps(payload)}\n\n"


@pytest.fixture(autouse=True)
def _disable_http_bridge(monkeypatch: pytest.MonkeyPatch) -> None:
    app_settings = Settings(
        http_responses_session_bridge_enabled=False,
        proxy_request_budget_seconds=75.0,
        compact_request_budget_seconds=75.0,
        transcription_request_budget_seconds=120.0,
        upstream_compact_timeout_seconds=None,
        upstream_stream_transport="auto",
        log_proxy_request_payload=False,
        log_proxy_request_shape=False,
        log_proxy_request_shape_raw_cache_key=False,
        log_proxy_service_tier_trace=False,
        stream_idle_timeout_seconds=300.0,
        proxy_token_refresh_limit=32,
        proxy_upstream_websocket_connect_limit=64,
        proxy_response_create_limit=64,
        proxy_compact_response_create_limit=16,
    )
    dashboard_settings = DashboardSettings(
        id=1,
        sticky_threads_enabled=False,
        upstream_stream_transport="auto",
        prefer_earlier_reset_accounts=False,
        routing_strategy="usage_weighted",
        openai_cache_affinity_max_age_seconds=300,
        import_without_overwrite=False,
        totp_required_on_login=False,
        api_key_auth_enabled=False,
        http_responses_session_bridge_prompt_cache_idle_ttl_seconds=3600,
        http_responses_session_bridge_gateway_safe_mode=False,
        sticky_reallocation_budget_threshold_pct=95.0,
    )

    class _SettingsCache:
        async def get(self) -> DashboardSettings:
            return dashboard_settings

    monkeypatch.setattr(proxy_module, "get_settings_cache", lambda: _SettingsCache())
    monkeypatch.setattr(proxy_module, "get_settings", lambda: app_settings)
