from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

import app.modules.proxy.anthropic_service as anthropic_proxy_module
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, StickySession, StickySessionKind
from app.db.session import SessionLocal
from app.dependencies import get_anthropic_proxy_service_for_app
from app.modules.proxy.load_balancer import AccountSelection, RuntimeState
from tests.integration.anthropic_proxy_support import (
    _insert_account,
    _insert_quota_cooldown,
)
from tests.integration.anthropic_proxy_support import (
    eastern_local_time as eastern_local_time,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
@pytest.mark.parametrize("runtime_only", [False, True])
async def test_anthropic_session_route_surfaces_provider_status_failure(async_client, app_instance, runtime_only):
    for index in range(3):
        await _insert_account(
            account_id=f"anthropic-rate-limited-{index}",
            provider="anthropic",
            access_token=f"anthropic-access-{index}",
            email=f"claude-{index}@example.com",
            status=AccountStatus.ACTIVE if runtime_only else AccountStatus.RATE_LIMITED,
        )

    blocked_at = int(time.time())
    retry_at = blocked_at + 60
    if runtime_only:
        service = get_anthropic_proxy_service_for_app(app_instance)
        for index in range(3):
            service._load_balancer._runtime[f"anthropic-rate-limited-{index}"] = RuntimeState(
                cooldown_until=retry_at,
            )
    async with SessionLocal() as session:
        for index in range(0 if runtime_only else 3):
            account = await session.get(Account, f"anthropic-rate-limited-{index}")
            account.blocked_at = blocked_at
            account.reset_at = retry_at
        await session.commit()

    response = await async_client.post(
        "/api/anthropic/session-route",
        json={
            "sessionId": "session-route-rate-limited-accounts",
            "model": "claude-fable-5",
            "quotaKey": "anthropic_top_thinking",
        },
    )

    assert response.status_code == 503
    error = response.json()["error"]
    status = "active" if runtime_only else "rate_limited"
    assert f"statuses: {status}=3" in error["message"]
    assert f"Limits reset at {datetime.fromtimestamp(retry_at).isoformat()}." in error["message"]
    if runtime_only:
        assert "selector reason: Rate limit exceeded. Try again in" in error["message"]
        assert "runtime cooldown excluded 3 accounts" in error["message"]
    assert error["type"] == "server_error"
    assert error["code"] == "no_available_anthropic_accounts"
    assert error["retryAt"] == datetime.fromtimestamp(retry_at, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    assert 0 < error["retryAfterSeconds"] <= 60


@pytest.mark.asyncio
async def test_anthropic_messages_quota_cooldown_returns_native_rate_limit(async_client, eastern_local_time):

    await _insert_account(
        account_id="anthropic-cooling",
        provider="anthropic",
        access_token="anthropic-access",
        email="claude@example.com",
    )
    reset_at = int((utcnow() + timedelta(minutes=7)).replace(tzinfo=timezone.utc).timestamp())
    await _insert_quota_cooldown(
        account_id="anthropic-cooling",
        quota_key="anthropic_top_thinking",
        reset_at=reset_at,
    )

    response = await async_client.post(
        "/v1/messages",
        json={
            "model": "claude-fable-5",
            "max_tokens": 32,
            "messages": [{"role": "user", "content": "hello"}],
            "thinking": {"type": "adaptive"},
        },
        headers={"anthropic-beta": "oauth-2025-04-20"},
    )

    assert response.status_code == 429
    body = response.json()
    assert body["type"] == "error"
    assert body["error"]["type"] == "rate_limit_error"
    assert "cooling down" in body["error"]["message"]

    retry_at = int(response.headers["anthropic-ratelimit-unified-reset"])
    assert f"Reset at {datetime.fromtimestamp(retry_at, tz=timezone.utc).isoformat()}." in body["error"]["message"]
    assert reset_at - retry_at >= 5 * 60
    assert 0 < retry_at - int(time.time()) <= 60
    assert 0 < int(response.headers["retry-after"]) <= 7 * 60


@pytest.mark.asyncio
async def test_anthropic_session_route_includes_retry_metadata(async_client):
    await _insert_account(
        account_id="anthropic-cooling",
        provider="anthropic",
        access_token="anthropic-access",
        email="claude@example.com",
    )
    reset_at = int((utcnow() + timedelta(minutes=7)).replace(tzinfo=timezone.utc).timestamp())
    await _insert_quota_cooldown(
        account_id="anthropic-cooling",
        quota_key="anthropic_top_thinking",
        reset_at=reset_at,
    )

    response = await async_client.post(
        "/api/anthropic/session-route",
        json={
            "sessionId": "session-route-cooldown",
            "model": "claude-fable-5",
            "quotaKey": "anthropic_top_thinking",
        },
    )

    assert response.status_code == 429
    error = response.json()["error"]
    assert error["code"] == "anthropic_quota_cooldown"

    assert error["retryAt"] == datetime.fromtimestamp(
        int(time.time()) + error["retryAfterSeconds"], tz=timezone.utc
    ).isoformat().replace("+00:00", "Z")
    assert 0 < error["retryAfterSeconds"] <= 60
    assert response.headers["retry-after"] == str(error["retryAfterSeconds"])


@pytest.mark.asyncio
async def test_anthropic_session_route_accepts_fast_quota_with_model_affinity(async_client):
    await _insert_account(
        account_id="anthropic-fast-preflight",
        provider="anthropic",
        access_token="anthropic-access",
        email="claude@example.com",
    )

    response = await async_client.post(
        "/api/anthropic/session-route",
        json={
            "sessionId": "session-route-fast",
            "model": "claude-fable-5",
            "quotaKey": "anthropic_fast",
            "affinityQuotaKey": "anthropic_top_thinking",
        },
    )

    assert response.status_code == 200
    assert response.json()["quotaKey"] == "anthropic_fast"
    assert response.json()["affinityQuotaKey"] == "anthropic_top_thinking"

    sticky_key = "claude:anthropic_top_thinking:session:" + anthropic_proxy_module._hash_for_key("session-route-fast")
    async with SessionLocal() as session:
        sticky = (
            await session.execute(
                select(StickySession).where(
                    StickySession.key == sticky_key,
                    StickySession.kind == StickySessionKind.CODEX_SESSION,
                )
            )
        ).scalar_one()

    assert sticky.account_id == "anthropic-fast-preflight"


@pytest.mark.asyncio
async def test_anthropic_session_route_accepts_explicit_fable_affinity_quota_key(async_client):
    await _insert_account(
        account_id="anthropic-fable-affinity-preflight",
        provider="anthropic",
        access_token="anthropic-access-fable-affinity",
        email="fable-affinity@example.com",
    )

    response = await async_client.post(
        "/api/anthropic/session-route",
        json={
            "sessionId": "session-route-fable-affinity",
            "model": "claude-fable-5",
            "quotaKey": "anthropic_top_thinking",
            "affinityQuotaKey": "anthropic_top_thinking_fable",
        },
    )

    assert response.status_code == 200
    assert response.json()["quotaKey"] == "anthropic_top_thinking"
    assert response.json()["affinityQuotaKey"] == "anthropic_top_thinking_fable"


@pytest.mark.asyncio
async def test_anthropic_session_route_explains_active_account_blocked_by_model_quota(
    async_client,
    monkeypatch,
):
    await _insert_account(
        account_id="anthropic-active-cooling",
        provider="anthropic",
        access_token="anthropic-access-active",
        email="active-claude@example.com",
    )
    await _insert_account(
        account_id="anthropic-quota-exceeded",
        provider="anthropic",
        access_token="anthropic-access-exhausted",
        email="exhausted-claude@example.com",
        status=AccountStatus.QUOTA_EXCEEDED,
    )
    reset_at = int((utcnow() + timedelta(minutes=4)).replace(tzinfo=timezone.utc).timestamp())
    await _insert_quota_cooldown(
        account_id="anthropic-active-cooling",
        quota_key="anthropic_top",
        reset_at=reset_at,
    )

    async def fake_select_account(self, **kwargs):
        del self
        assert kwargs["account_ids"] == ["anthropic-quota-exceeded"]
        return AccountSelection(
            account=None,
            error_message="No available accounts",
            error_code="no_available_anthropic_accounts",
        )

    monkeypatch.setattr(anthropic_proxy_module.LoadBalancer, "select_account", fake_select_account)

    response = await async_client.post(
        "/api/anthropic/session-route",
        json={
            "sessionId": "session-route-active-cooling",
            "model": "claude-fable-5",
            "quotaKey": "anthropic_top",
        },
    )

    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "no_available_anthropic_accounts"
    assert "statuses: active=1, quota_exceeded=1" in error["message"]
    assert "Model quota: anthropic_top cooldown excluded 1 account" in error["message"]
    assert "1 account remained after the anthropic_top prefilter" in error["message"]

    retry_at = datetime.fromisoformat(error["retryAt"].replace("Z", "+00:00")).timestamp()
    assert 0 < retry_at - time.time() <= 60
    assert 0 < error["retryAfterSeconds"] <= 4 * 60


@pytest.mark.asyncio
async def test_anthropic_messages_all_routable_accounts_cooling_returns_429_despite_unusable_rows(async_client):

    await _insert_account(
        account_id="anthropic-usable-cooling",
        provider="anthropic",
        access_token="anthropic-access-usable",
        email="usable@example.com",
    )
    await _insert_account(
        account_id="anthropic-canceled-sub",
        provider="anthropic",
        access_token="anthropic-access-canceled",
        email="canceled@example.com",
        subscription_status="canceled",
    )
    await _insert_account(
        account_id="anthropic-deactivated",
        provider="anthropic",
        access_token="anthropic-access-deactivated",
        email="deactivated@example.com",
        status=AccountStatus.DEACTIVATED,
    )
    reset_at = int((utcnow() + timedelta(minutes=6)).replace(tzinfo=timezone.utc).timestamp())
    await _insert_quota_cooldown(
        account_id="anthropic-usable-cooling",
        quota_key="anthropic_top_thinking",
        reset_at=reset_at,
    )

    response = await async_client.post(
        "/v1/messages",
        json={
            "model": "claude-fable-5",
            "max_tokens": 32,
            "messages": [{"role": "user", "content": "hello"}],
            "thinking": {"type": "adaptive"},
        },
        headers={"anthropic-beta": "oauth-2025-04-20"},
    )

    assert response.status_code == 429
    body = response.json()
    assert body["type"] == "error"
    assert body["error"]["type"] == "rate_limit_error"
    assert "cooling down" in body["error"]["message"]

    assert 0 < int(response.headers["anthropic-ratelimit-unified-reset"]) - time.time() <= 60
    assert 0 < int(response.headers["retry-after"]) <= 6 * 60


@pytest.mark.asyncio
async def test_anthropic_session_route_failure_counts_only_routable_accounts(async_client, monkeypatch):

    await _insert_account(
        account_id="anthropic-routable",
        provider="anthropic",
        access_token="anthropic-access-routable",
        email="routable@example.com",
        status=AccountStatus.RATE_LIMITED,
    )
    await _insert_account(
        account_id="anthropic-canceled",
        provider="anthropic",
        access_token="anthropic-access-canceled",
        email="canceled@example.com",
        subscription_status="canceled",
    )
    await _insert_account(
        account_id="anthropic-deactivated",
        provider="anthropic",
        access_token="anthropic-access-deactivated",
        email="deactivated@example.com",
        status=AccountStatus.DEACTIVATED,
    )

    async def fake_select_account(self, **kwargs):
        del self

        assert kwargs["account_ids"] == ["anthropic-routable"]
        return AccountSelection(
            account=None,
            error_message="No available accounts",
            error_code="no_available_anthropic_accounts",
        )

    monkeypatch.setattr(anthropic_proxy_module.LoadBalancer, "select_account", fake_select_account)

    response = await async_client.post(
        "/api/anthropic/session-route",
        json={
            "sessionId": "session-route-routable-count",
            "model": "claude-fable-5",
            "quotaKey": "anthropic_top",
        },
    )

    assert response.status_code == 503
    message = response.json()["error"]["message"]
    assert "1 Anthropic account exist" in message
    assert "statuses: rate_limited=1." in message
    assert "deactivated=1" not in message
    assert "+2 stored but not routable" in message
