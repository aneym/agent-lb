from __future__ import annotations

import base64
import json
from types import SimpleNamespace

import pytest

import app.modules.proxy.service as proxy_module


class _SettingsCache:
    def __init__(self, settings: object) -> None:
        self._settings = settings

    async def get(self) -> object:
        return self._settings


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


async def _import_account(async_client, account_id: str, email: str) -> str:
    auth_json = _make_auth_json(account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200
    payload = response.json()
    return payload["accountId"]


async def _set_routing_settings(
    async_client,
    *,
    sticky_threads_enabled: bool,
    prefer_earlier_reset_accounts: bool = False,
    routing_strategy: str = "usage_weighted",
) -> None:
    response = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": sticky_threads_enabled,
            "preferEarlierResetAccounts": prefer_earlier_reset_accounts,
            "routingStrategy": routing_strategy,
        },
    )
    assert response.status_code == 200


def _install_proxy_settings_cache(
    monkeypatch: pytest.MonkeyPatch,
    *,
    sticky_threads_enabled: bool,
    prefer_earlier_reset_accounts: bool = False,
    openai_cache_affinity_max_age_seconds: int = 300,
    sticky_reallocation_budget_threshold_pct: float = 95.0,
    openai_prompt_cache_key_derivation_enabled: bool = True,
) -> None:
    settings = SimpleNamespace(
        prefer_earlier_reset_accounts=prefer_earlier_reset_accounts,
        sticky_threads_enabled=sticky_threads_enabled,
        openai_cache_affinity_max_age_seconds=openai_cache_affinity_max_age_seconds,
        sticky_reallocation_budget_threshold_pct=sticky_reallocation_budget_threshold_pct,
        openai_prompt_cache_key_derivation_enabled=openai_prompt_cache_key_derivation_enabled,
        routing_strategy="usage_weighted",
        proxy_request_budget_seconds=75.0,
        compact_request_budget_seconds=75.0,
        transcription_request_budget_seconds=120.0,
        upstream_compact_timeout_seconds=None,
        upstream_stream_transport="auto",
        log_proxy_request_payload=False,
        log_proxy_request_shape=False,
        log_proxy_request_shape_raw_cache_key=False,
        log_proxy_service_tier_trace=False,
        http_responses_session_bridge_enabled=False,
        http_responses_session_bridge_idle_ttl_seconds=120.0,
        http_responses_session_bridge_codex_idle_ttl_seconds=900.0,
        http_responses_session_bridge_max_sessions=128,
        http_responses_session_bridge_queue_limit=8,
        http_responses_session_bridge_response_create_concurrency=64,
        http_responses_session_bridge_instance_id="test-instance",
        http_responses_session_bridge_instance_ring=[],
        http_responses_session_bridge_advertise_base_url=None,
        http_responses_session_bridge_prompt_cache_idle_ttl_seconds=3600,
        http_responses_session_bridge_gateway_safe_mode=False,
        proxy_token_refresh_limit=32,
        proxy_upstream_websocket_connect_limit=64,
        proxy_response_create_limit=64,
        proxy_compact_response_create_limit=16,
    )
    monkeypatch.setattr(proxy_module, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(proxy_module, "get_settings", lambda: settings)
