from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.modules.oauth.service as oauth_module
from app.core.auth import generate_unique_account_id
from app.core.clients.oauth import DeviceCode, OAuthTokens
from app.core.crypto import TokenEncryptor
from app.core.upstream_proxy import UpstreamProxyRouteError
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository
from tests.integration.oauth_flow_support import (
    _encode_jwt,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_device_oauth_flow_creates_account(async_client, monkeypatch):
    await oauth_module._OAUTH_STORE.reset()

    email = "device@example.com"
    raw_account_id = "acc_device"

    async def fake_device_code(**_):
        return DeviceCode(
            verification_url="https://auth.openai.com/codex/device",
            user_code="ABCD-EFGH",
            device_auth_id="dev_123",
            interval_seconds=1,
            expires_in_seconds=30,
        )

    async def fake_exchange_device_token(**_):
        payload = {
            "email": email,
            "chatgpt_account_id": raw_account_id,
            "https://api.openai.com/auth": {"chatgpt_plan_type": "plus"},
        }
        return OAuthTokens(
            access_token="access-token",
            refresh_token="refresh-token",
            id_token=_encode_jwt(payload),
        )

    async def fake_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(oauth_module, "request_device_code", fake_device_code)
    monkeypatch.setattr(oauth_module, "exchange_device_token", fake_exchange_device_token)
    monkeypatch.setattr(oauth_module, "_async_sleep", fake_sleep)

    start = await async_client.post("/api/oauth/start", json={"forceMethod": "device"})
    assert start.status_code == 200
    assert start.json()["method"] == "device"

    await asyncio.sleep(0)

    payload = None
    for _ in range(20):
        status = await async_client.get("/api/oauth/status")
        assert status.status_code == 200
        payload = status.json()
        if payload["status"] == "success":
            break
        await asyncio.sleep(0.05)
    assert payload and payload["status"] == "success"

    expected_account_id = generate_unique_account_id(raw_account_id, email)
    accounts = await async_client.get("/api/accounts")
    assert accounts.status_code == 200
    data = accounts.json()["accounts"]
    assert any(account["accountId"] == expected_account_id for account in data)


@pytest.mark.asyncio
async def test_starting_new_device_flow_cancels_previous_pending_poll(async_client, monkeypatch):
    await oauth_module._OAUTH_STORE.reset()
    issued = 0

    async def fake_device_code(**_):
        nonlocal issued
        issued += 1
        return DeviceCode(
            verification_url="https://auth.openai.com/codex/device",
            user_code=f"CODE-{issued}",
            device_auth_id=f"dev_{issued}",
            interval_seconds=30,
            expires_in_seconds=300,
        )

    async def fake_exchange_device_token(**_):
        await asyncio.sleep(300)
        raise AssertionError("device token polling should be cancelled by the test")

    monkeypatch.setattr(oauth_module, "request_device_code", fake_device_code)
    monkeypatch.setattr(oauth_module, "exchange_device_token", fake_exchange_device_token)

    first = await async_client.post("/api/oauth/start", json={"forceMethod": "device"})
    assert first.status_code == 200
    await asyncio.sleep(0)
    async with oauth_module._OAUTH_STORE.lock:
        first_flow_id = first.json()["flowId"]
        first_flow = oauth_module._OAUTH_STORE.get_flow_locked(first_flow_id)
        assert first_flow is not None
        first_task = first_flow.poll_task
        assert first_task is not None

    second = await async_client.post("/api/oauth/start", json={"forceMethod": "device"})
    assert second.status_code == 200
    second_flow_id = second.json()["flowId"]
    await asyncio.sleep(0)

    async with oauth_module._OAUTH_STORE.lock:
        pending_device_flows = [
            flow
            for flow in oauth_module._OAUTH_STORE._flows.values()
            if flow.method == "device" and flow.status == "pending"
        ]
        assert [flow.flow_id for flow in pending_device_flows] == [second_flow_id]
        assert oauth_module._OAUTH_STORE.get_flow_locked(first_flow_id) is None
    assert first_task.cancelled()

    await oauth_module._OAUTH_STORE.reset()


@pytest.mark.asyncio
async def test_device_oauth_reauth_reuses_existing_row_for_same_chatgpt_identity(
    async_client,
    monkeypatch,
):

    await oauth_module._OAUTH_STORE.reset()

    settings = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "importWithoutOverwrite": True,
            "totpRequiredOnLogin": False,
        },
    )
    assert settings.status_code == 200
    assert settings.json()["importWithoutOverwrite"] is True

    email = "device-reauth@example.com"
    raw_account_id = "acc_device_reauth"

    async def fake_device_code(**_):
        return DeviceCode(
            verification_url="https://auth.openai.com/codex/device",
            user_code="ABCD-EFGH",
            device_auth_id="dev_sep",
            interval_seconds=1,
            expires_in_seconds=30,
        )

    call_count = {"value": 0}

    async def fake_exchange_device_token(**_):
        call_count["value"] += 1
        plan_type = "plus" if call_count["value"] == 1 else "team"
        payload = {
            "email": email,
            "chatgpt_account_id": raw_account_id,
            "https://api.openai.com/auth": {"chatgpt_plan_type": plan_type},
        }
        return OAuthTokens(
            access_token=f"access-token-{call_count['value']}",
            refresh_token=f"refresh-token-{call_count['value']}",
            id_token=_encode_jwt(payload),
        )

    async def fake_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(oauth_module, "request_device_code", fake_device_code)
    monkeypatch.setattr(oauth_module, "exchange_device_token", fake_exchange_device_token)
    monkeypatch.setattr(oauth_module, "_async_sleep", fake_sleep)

    async def _run_device_flow_once() -> None:
        start = await async_client.post("/api/oauth/start", json={"forceMethod": "device"})
        assert start.status_code == 200
        assert start.json()["method"] == "device"

        complete = await async_client.post("/api/oauth/complete", json={})
        assert complete.status_code == 200
        assert complete.json()["status"] == "pending"

        await asyncio.sleep(0)

        payload = None
        for _ in range(20):
            status = await async_client.get("/api/oauth/status")
            assert status.status_code == 200
            payload = status.json()
            if payload["status"] == "success":
                break
            await asyncio.sleep(0.05)
        assert payload and payload["status"] == "success"

    await _run_device_flow_once()
    await _run_device_flow_once()

    accounts = await async_client.get("/api/accounts")
    assert accounts.status_code == 200
    data = [account for account in accounts.json()["accounts"] if account["email"] == email]
    assert len(data) == 1
    base_id = generate_unique_account_id(raw_account_id, email)
    assert data[0]["accountId"] == base_id

    assert data[0]["planType"] == "team"


@pytest.mark.asyncio
async def test_device_oauth_flow_heals_deactivated_account_when_import_without_overwrite_enabled(
    async_client,
    monkeypatch,
):
    await oauth_module._OAUTH_STORE.reset()

    settings = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "importWithoutOverwrite": True,
            "totpRequiredOnLogin": False,
        },
    )
    assert settings.status_code == 200
    assert settings.json()["importWithoutOverwrite"] is True

    email = "device-reauth@example.com"
    raw_account_id = "acc_device_reauth"
    account_id = generate_unique_account_id(raw_account_id, email)

    encryptor = TokenEncryptor()
    existing = Account(
        id=account_id,
        chatgpt_account_id=raw_account_id,
        email=email,
        plan_type="plus",
        routing_policy="preserve",
        access_token_encrypted=encryptor.encrypt("old-access"),
        refresh_token_encrypted=encryptor.encrypt("old-refresh"),
        id_token_encrypted=encryptor.encrypt("old-id"),
        last_refresh=utcnow(),
        status=AccountStatus.DEACTIVATED,
        deactivation_reason="refresh_failed",
    )
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        await repo.upsert(existing, merge_by_email=False)

    async def fake_device_code(**_):
        return DeviceCode(
            verification_url="https://auth.openai.com/codex/device",
            user_code="ABCD-EFGH",
            device_auth_id="dev_reauth",
            interval_seconds=1,
            expires_in_seconds=30,
        )

    async def fake_exchange_device_token(**_):
        payload = {
            "email": email,
            "chatgpt_account_id": raw_account_id,
            "https://api.openai.com/auth": {"chatgpt_plan_type": "pro"},
        }
        return OAuthTokens(
            access_token="new-access-token",
            refresh_token="new-refresh-token",
            id_token=_encode_jwt(payload),
        )

    async def fake_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(oauth_module, "request_device_code", fake_device_code)
    monkeypatch.setattr(oauth_module, "exchange_device_token", fake_exchange_device_token)
    monkeypatch.setattr(oauth_module, "_async_sleep", fake_sleep)

    start = await async_client.post("/api/oauth/start", json={"forceMethod": "device"})
    assert start.status_code == 200

    complete = await async_client.post("/api/oauth/complete", json={})
    assert complete.status_code == 200
    assert complete.json()["status"] == "pending"

    await asyncio.sleep(0)

    payload = None
    for _ in range(20):
        status = await async_client.get("/api/oauth/status")
        assert status.status_code == 200
        payload = status.json()
        if payload["status"] == "success":
            break
        await asyncio.sleep(0.05)
    assert payload and payload["status"] == "success"

    accounts = await async_client.get("/api/accounts")
    assert accounts.status_code == 200
    data = [account for account in accounts.json()["accounts"] if account["email"] == email]
    assert len(data) == 1
    healed = data[0]
    assert healed["accountId"] == account_id
    assert healed["status"] == "active"
    assert healed["deactivationReason"] is None
    assert healed["planType"] == "pro"
    assert healed["routingPolicy"] == "preserve"


@pytest.mark.asyncio
async def test_oauth_persist_tokens_invalidates_routing_caches_after_identity_merge(monkeypatch):
    repo = AsyncMock()
    service = oauth_module.OauthService(repo)
    account_cache = SimpleNamespace(invalidated=False)

    def _invalidate_account_cache() -> None:
        account_cache.invalidated = True

    account_cache.invalidate = _invalidate_account_cache
    api_key_cache = SimpleNamespace(cleared=False)

    def _clear_api_key_cache() -> None:
        api_key_cache.cleared = True

    api_key_cache.clear = _clear_api_key_cache
    poller = SimpleNamespace(bumped=[])

    async def _bump(namespace: str) -> None:
        poller.bumped.append(namespace)

    poller.bump = _bump
    monkeypatch.setattr(oauth_module, "get_account_selection_cache", lambda: account_cache, raising=False)
    monkeypatch.setattr(oauth_module, "get_api_key_cache", lambda: api_key_cache, raising=False)
    monkeypatch.setattr(oauth_module, "get_cache_invalidation_poller", lambda: poller, raising=False)
    monkeypatch.setattr(oauth_module, "NAMESPACE_API_KEY", "api_key", raising=False)

    payload = {
        "email": "reauth-cache@example.com",
        "chatgpt_account_id": "acc_reauth_cache",
        "https://api.openai.com/auth": {"chatgpt_plan_type": "plus"},
    }

    await service._persist_tokens(
        OAuthTokens(
            access_token="access-token",
            refresh_token="refresh-token",
            id_token=_encode_jwt(payload),
        )
    )

    repo.upsert.assert_awaited_once()
    assert account_cache.invalidated is True
    assert api_key_cache.cleared is True
    assert poller.bumped == ["api_key"]


@pytest.mark.asyncio
async def test_device_oauth_flow_keeps_same_email_distinct_upstream_identities_in_overwrite_mode(
    async_client,
    monkeypatch,
):
    await oauth_module._OAUTH_STORE.reset()

    enable_separate = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "importWithoutOverwrite": True,
            "totpRequiredOnLogin": False,
        },
    )
    assert enable_separate.status_code == 200
    assert enable_separate.json()["importWithoutOverwrite"] is True

    email = "oauth-conflict@example.com"

    async def fake_device_code(**_):
        return DeviceCode(
            verification_url="https://auth.openai.com/codex/device",
            user_code="ABCD-EFGH",
            device_auth_id="dev_conflict",
            interval_seconds=1,
            expires_in_seconds=30,
        )

    call_count = {"value": 0}

    async def fake_exchange_device_token(**_):

        call_count["value"] += 1
        if call_count["value"] == 1:
            account_id = "acc_oauth_conflict_one"
            plan_type = "plus"
        elif call_count["value"] == 2:
            account_id = "acc_oauth_conflict_two"
            plan_type = "team"
        else:
            account_id = "acc_oauth_conflict_new"
            plan_type = "pro"
        payload = {
            "email": email,
            "chatgpt_account_id": account_id,
            "https://api.openai.com/auth": {"chatgpt_plan_type": plan_type},
        }
        return OAuthTokens(
            access_token=f"access-token-{call_count['value']}",
            refresh_token=f"refresh-token-{call_count['value']}",
            id_token=_encode_jwt(payload),
        )

    async def fake_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(oauth_module, "request_device_code", fake_device_code)
    monkeypatch.setattr(oauth_module, "exchange_device_token", fake_exchange_device_token)
    monkeypatch.setattr(oauth_module, "_async_sleep", fake_sleep)

    async def _run_device_flow_once() -> dict[str, str | None]:
        start = await async_client.post("/api/oauth/start", json={"forceMethod": "device"})
        assert start.status_code == 200
        assert start.json()["method"] == "device"

        complete = await async_client.post("/api/oauth/complete", json={})
        assert complete.status_code == 200
        assert complete.json()["status"] == "pending"

        await asyncio.sleep(0)

        payload: dict[str, str | None] | None = None
        for _ in range(20):
            status = await async_client.get("/api/oauth/status")
            assert status.status_code == 200
            payload = status.json()
            if payload["status"] in {"success", "error"}:
                break
            await asyncio.sleep(0.05)
        assert payload is not None
        return payload

    assert (await _run_device_flow_once())["status"] == "success"
    assert (await _run_device_flow_once())["status"] == "success"

    enable_overwrite = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "importWithoutOverwrite": False,
            "totpRequiredOnLogin": False,
        },
    )
    assert enable_overwrite.status_code == 200
    assert enable_overwrite.json()["importWithoutOverwrite"] is False

    result = await _run_device_flow_once()
    assert result["status"] == "success"

    accounts = await async_client.get("/api/accounts")
    assert accounts.status_code == 200
    matching_accounts = [account for account in accounts.json()["accounts"] if account["email"] == email]
    assert {account["accountId"] for account in matching_accounts} == {
        generate_unique_account_id("acc_oauth_conflict_one", email),
        generate_unique_account_id("acc_oauth_conflict_two", email),
        generate_unique_account_id("acc_oauth_conflict_new", email),
    }


@pytest.mark.asyncio
async def test_device_oauth_flow_reports_proxy_route_errors(async_client, monkeypatch):
    await oauth_module._OAUTH_STORE.reset()

    async def fake_oauth_route(*_args, **_kwargs):
        raise UpstreamProxyRouteError("default_pool_unconfigured", account_id=None)

    monkeypatch.setattr(oauth_module, "resolve_upstream_route", fake_oauth_route)

    start = await async_client.post("/api/oauth/start", json={"forceMethod": "device"})

    assert start.status_code == 502
    assert start.json()["error"]["code"] == "default_pool_unconfigured"
