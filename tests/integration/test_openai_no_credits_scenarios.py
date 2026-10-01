"""OpenAI credits are never spent (Alex, 2026-10-01 14:3x ET: "dont use credits ever, you can use a reset").

Codex keeps answering on an account whose weekly or 5-hour window is used up by billing paid
credits. agent-lb counted credits_has / a positive balance as "usable", so on Oct 1 all five
accounts at 100% weekly kept serving (1,399 requests an hour, about 2,800 credits in six hours).
Now an OpenAI account with a spent window is out of rotation whatever its credit balance, the
same way anthropic_route_to_extra_usage=False keeps Anthropic extra usage off.

These drive the real proxy and accounts API; only the upstream compact call is faked.
"""

from __future__ import annotations

import base64
import json
import time

import pytest

import app.modules.proxy.service as proxy_module
from app.core.auth import generate_unique_account_id
from app.core.openai.models import CompactResponsePayload
from app.core.utils.time import utcnow
from app.db.models import AccountStatus
from app.db.session import SessionLocal
from app.modules.proxy.rate_limit_cache import get_rate_limit_headers_cache
from app.modules.usage.repository import UsageRepository

pytestmark = pytest.mark.integration


def _auth_json(account_id: str, email: str) -> dict:
    claims = {
        "email": email,
        "chatgpt_account_id": account_id,
        "https://api.openai.com/auth": {"chatgpt_plan_type": "pro"},
    }
    body = base64.urlsafe_b64encode(json.dumps(claims, separators=(",", ":")).encode()).rstrip(b"=").decode()
    return {
        "tokens": {
            "idToken": f"header.{body}.sig",
            "accessToken": "access-token",
            "refreshToken": "refresh-token",
            "accountId": account_id,
        }
    }


async def _import(async_client, raw_id: str, email: str) -> str:
    files = {"auth_json": ("auth.json", json.dumps(_auth_json(raw_id, email)), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200
    return generate_unique_account_id(raw_id, email)


async def _usage(account_id: str, *, five_hour: float, weekly: float) -> None:
    now = int(time.time())
    async with SessionLocal() as session:
        repo = UsageRepository(session)
        for window, used, minutes in (("primary", five_hour, 300), ("secondary", weekly, 10080)):
            await repo.add_entry(
                account_id=account_id,
                used_percent=used,
                window=window,
                window_minutes=minutes,
                reset_at=now + minutes * 60,
                recorded_at=utcnow(),
                credits_has=True,
                credits_unlimited=False,
                credits_balance=60000.0,
            )
    await get_rate_limit_headers_cache().invalidate()


def _fake_upstream(monkeypatch) -> list[str]:
    attempted: list[str] = []

    async def fake_compact(payload, headers, access_token, account_id):
        del payload, headers, access_token
        attempted.append(account_id)
        return CompactResponsePayload.model_validate({"object": "response.compaction", "output": []})

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)
    return attempted


async def _compact(async_client):
    return await async_client.post(
        "/backend-api/codex/responses/compact",
        json={"model": "gpt-5.1", "instructions": "hi", "input": []},
    )


@pytest.mark.asyncio
async def test_a_spent_weekly_window_with_credits_is_never_served(async_client, monkeypatch):
    attempted = _fake_upstream(monkeypatch)
    spent = await _import(async_client, "acc_weekly_spent", "weekly-spent@example.com")
    await _usage(spent, five_hour=10.0, weekly=100.0)

    response = await _compact(async_client)
    assert attempted == []
    assert response.status_code >= 400

    fresh = await _import(async_client, "acc_weekly_fresh", "weekly-fresh@example.com")
    await _usage(fresh, five_hour=10.0, weekly=20.0)
    response = await _compact(async_client)
    assert response.status_code == 200
    assert attempted == ["acc_weekly_fresh"]


@pytest.mark.asyncio
async def test_a_spent_five_hour_window_with_credits_is_never_served(async_client, monkeypatch):
    attempted = _fake_upstream(monkeypatch)
    spent = await _import(async_client, "acc_5h_spent", "five-hour-spent@example.com")
    await _usage(spent, five_hour=100.0, weekly=30.0)
    response = await _compact(async_client)
    assert attempted == []
    assert response.status_code >= 400


@pytest.mark.asyncio
async def test_accounts_api_reports_the_spent_account_as_out_of_quota(async_client):
    spent = await _import(async_client, "acc_status_spent", "status-spent@example.com")
    await _usage(spent, five_hour=10.0, weekly=100.0)
    response = await async_client.get("/api/accounts")
    assert response.status_code == 200
    account = next(item for item in response.json()["accounts"] if item["accountId"] == spent)
    assert account["status"] == AccountStatus.QUOTA_EXCEEDED.value


def test_the_quota_rule_ignores_credits_for_openai():
    from app.core.usage.quota import apply_usage_quota

    status, used, _ = apply_usage_quota(
        status=AccountStatus.ACTIVE,
        primary_used=10.0,
        primary_reset=None,
        primary_window_minutes=300,
        runtime_reset=None,
        secondary_used=100.0,
        secondary_reset=int(time.time()) + 3600,
        credits_has=True,
        credits_unlimited=False,
        credits_balance=60000.0,
    )
    assert status == AccountStatus.QUOTA_EXCEEDED
    assert used == 100.0


# A Codex session keeps one upstream bridge open per prompt-cache key. Until now the bridge
# reused it on the account status it captured when the session opened, so pausing an account
# (or its window running out) did not move sessions already on it: paused accounts kept serving
# for hours on Oct 1. The bridge now re-checks the account before every reuse.


async def _bridge_two_accounts(async_client, monkeypatch, prefix: str) -> list[str]:
    from tests.integration import test_http_responses_bridge as bridge

    bridge._install_bridge_settings(monkeypatch, enabled=True)
    await _import(async_client, f"acc_{prefix}_one", f"{prefix}-one@example.com")
    await _import(async_client, f"acc_{prefix}_two", f"{prefix}-two@example.com")
    connected: list[str] = []

    async def fresh(self, target, *, force=False, timeout_seconds):
        del self, force, timeout_seconds
        return target

    async def connect(headers, access_token, account_id_header, *, base_url=None, session=None):
        del headers, access_token, base_url, session
        connected.append(account_id_header)
        return bridge._FakeBridgeUpstreamWebSocket()

    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fresh)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", connect)
    return connected


async def _bridge_turn(async_client, key: str, text: str):
    return await async_client.post(
        "/v1/responses",
        json={"model": "gpt-5.1", "instructions": "Return exactly OK.", "input": text, "prompt_cache_key": key},
    )


async def _drop_bridge_sessions(app_instance) -> None:
    from tests.integration import test_http_responses_bridge as bridge

    service = bridge.get_proxy_service_for_app(app_instance)
    async with service._http_bridge_lock:
        sessions = list(service._http_bridge_sessions.values())
        service._http_bridge_sessions.clear()
        service._http_bridge_inflight_sessions.clear()
        service._http_bridge_turn_state_index.clear()
        service._http_bridge_previous_response_index.clear()
    for session in sessions:
        await service._close_http_bridge_session(session)


def _local_id(raw: str, prefix: str) -> str:
    return generate_unique_account_id(raw, f"{prefix}-{raw.rsplit('_', 1)[1]}@example.com")


@pytest.mark.asyncio
async def test_an_open_codex_session_leaves_an_account_once_it_is_paused(async_client, app_instance, monkeypatch):
    connected = await _bridge_two_accounts(async_client, monkeypatch, "bridge-pause")
    try:
        first = await _bridge_turn(async_client, "bridge-pause-key", "hello")
        assert first.status_code == 200
        assert len(connected) == 1
        first_raw = connected[0]

        paused = await async_client.post(f"/api/accounts/{_local_id(first_raw, 'bridge-pause')}/pause")
        assert paused.status_code == 200

        second = await _bridge_turn(async_client, "bridge-pause-key", "hello again")
        assert second.status_code == 200
        assert len(connected) == 2, "the paused account's open session was reused"
        assert connected[1] != first_raw
    finally:
        await _drop_bridge_sessions(app_instance)


@pytest.mark.asyncio
async def test_an_open_codex_session_leaves_an_account_whose_window_runs_out(async_client, app_instance, monkeypatch):
    connected = await _bridge_two_accounts(async_client, monkeypatch, "bridge-spent")
    try:
        first = await _bridge_turn(async_client, "bridge-spent-key", "hello")
        assert first.status_code == 200
        first_raw = connected[0]

        # The weekly window runs out mid-session; the account still holds paid credits.
        await _usage(_local_id(first_raw, "bridge-spent"), five_hour=10.0, weekly=100.0)

        second = await _bridge_turn(async_client, "bridge-spent-key", "hello again")
        assert second.status_code == 200
        assert len(connected) == 2, "the spent account's open session was reused"
        assert connected[1] != first_raw
    finally:
        await _drop_bridge_sessions(app_instance)
