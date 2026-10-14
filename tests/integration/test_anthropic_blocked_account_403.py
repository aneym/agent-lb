

import time

import pytest
from aiohttp import web

from app.core.config.settings import get_settings
from app.db.models import Account, AccountStatus, StickySessionKind
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.accounts.subscription_status import is_subscription_usable
from app.modules.proxy.load_balancer import LoadBalancer
from app.modules.proxy.sticky_repository import StickySessionsRepository
from app.modules.usage.repository import UsageRepository
from app.modules.usage.updater import UsageUpdater
from tests.integration.test_anthropic_proxy import ANTHROPIC_JSON_BYTES, _insert_account
from tests.integration.test_load_balancer_integration import _repo_factory

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_fifty_selections_reject_blocked_sticky_accounts(db_setup):
    for account_id, status, subscription in [
        ("A", AccountStatus.ACTIVE, "active"),
        ("B", AccountStatus.ACTIVE, "canceled"),
        ("C", AccountStatus.QUOTA_EXCEEDED, "active"),
        ("D", AccountStatus.PAUSED, "active"),
    ]:
        await _insert_account(
            account_id=account_id,
            provider="anthropic",
            access_token=account_id,
            email=f"{account_id}@example.invalid",
            status=status,
            subscription_status=subscription,
        )
    async with SessionLocal() as session:
        await UsageRepository(session).add_entry(
            account_id="C",
            provider="anthropic",
            window="secondary",
            used_percent=100.0,
            reset_at=int(time.time()) + 86400,
            window_minutes=10080,
        )
        account = await session.get(Account, "C")
        account.reset_at = int(time.time()) + 86400
        await session.commit()
    balancer = LoadBalancer(_repo_factory)
    for index in range(50):
        key = f"session-{index}"
        async with SessionLocal() as session:
            await StickySessionsRepository(session).upsert(
                key, "B" if index % 2 else "C", kind=StickySessionKind.CODEX_SESSION
            )
        selection = await balancer.select_account(
            provider="anthropic",
            sticky_key=key,
            sticky_kind=StickySessionKind.CODEX_SESSION,
            routing_strategy="usage_weighted",
        )
        assert selection.account is not None
        assert selection.account.id == "A"


@pytest.mark.parametrize("stream", [False, True])
async def test_organization_403_blocks_fails_over_and_refresh_recovers(async_client, monkeypatch, stream):
    for account_id in ("A", "B"):
        await _insert_account(
            account_id=account_id, provider="anthropic", access_token=account_id, email=f"{account_id}@example.invalid"
        )
    seen = []
    denied = None

    async def messages(request):
        nonlocal denied
        token = request.headers["Authorization"]
        seen.append(token)
        if denied is None:
            denied = token
            return web.json_response(
                {"error": {"message": "OAuth authentication is currently not allowed for this organization."}},
                status=403,
            )
        assert token != denied
        return web.Response(body=ANTHROPIC_JSON_BYTES, content_type="application/json")

    async def usage(request):
        return web.json_response({"five_hour": {"utilization": 5.0}, "seven_day": {"utilization": 5.0}})

    upstream = web.Application()
    upstream.router.add_post("/v1/messages", messages)
    upstream.router.add_get("/api/oauth/usage", usage)
    runner = web.AppRunner(upstream)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]
    monkeypatch.setattr(get_settings(), "anthropic_upstream_base_url", f"http://127.0.0.1:{port}")
    try:
        payload = {
            "model": "claude-sonnet-4-6",
            "max_tokens": 16,
            "messages": [{"role": "user", "content": "hi"}],
            "stream": stream,
        }
        response = await async_client.post("/v1/messages", json=payload)
        assert response.status_code == 200
        assert len(seen) == 2
        assert seen[0] != seen[1]
        async with SessionLocal() as session:
            blocked = await session.get(Account, denied.removeprefix("Bearer "))
            assert not is_subscription_usable(blocked)
            assert blocked.subscription_status is None
        response = await async_client.post("/v1/messages", json=payload)
        assert response.status_code == 200
        assert seen[2] == seen[1]
        monkeypatch.setattr(get_settings(), "usage_refresh_enabled", True)
        async with SessionLocal() as session:
            repo = AccountsRepository(session)
            blocked = await repo.get_by_id(denied.removeprefix("Bearer "))
            session.expunge(blocked)
            assert await UsageUpdater(UsageRepository(session), repo).force_refresh(blocked)
        async with SessionLocal() as session:
            recovered = await session.get(Account, denied.removeprefix("Bearer "))
            assert is_subscription_usable(recovered)
            assert recovered.deactivation_reason is None
        selected = await LoadBalancer(_repo_factory).select_account(provider="anthropic", account_ids=[recovered.id])
        assert selected.account is not None
        assert selected.account.id == recovered.id
    finally:
        await runner.cleanup()


@pytest.mark.parametrize("blocked_kind", ["canceled", "weekly", "paused"])
async def test_blocked_pool_returns_local_429(async_client, blocked_kind):
    await _insert_account(
        account_id="blocked",
        provider="anthropic",
        access_token="blocked",
        email="blocked@example.invalid",
        subscription_status="canceled" if blocked_kind == "canceled" else "active",
        status=AccountStatus.PAUSED if blocked_kind == "paused" else AccountStatus.QUOTA_EXCEEDED,
    )
    if blocked_kind == "weekly":
        async with SessionLocal() as session:
            await UsageRepository(session).add_entry(
                account_id="blocked",
                provider="anthropic",
                window="secondary",
                used_percent=100.0,
                reset_at=int(time.time()) + 86400,
                window_minutes=10080,
            )
    response = await async_client.post(
        "/v1/messages",
        json={"model": "claude-sonnet-4-6", "max_tokens": 16, "messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 429
    assert response.json()["type"] == "error"
