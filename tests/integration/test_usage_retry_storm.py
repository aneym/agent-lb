from __future__ import annotations

import time
from collections import Counter
from datetime import timedelta, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio
from aiohttp import web

from app.core.clients.http import close_http_client, init_http_client
from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.core.usage.refresh_scheduler import UsageRefreshScheduler
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal
from app.modules.accounts.subscription_status import ANTHROPIC_OAUTH_ORGANIZATION_BLOCK
from app.modules.usage import updater as updater_module
from app.modules.usage.repository import UsageRepository
from app.modules.usage.updater import UsageUpdater

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def usage_endpoint(monkeypatch, unused_tcp_port):
    calls = Counter()
    statuses = {}

    async def usage(request):
        account_id = request.headers["Authorization"].removeprefix("Bearer ")
        calls[account_id] += 1
        status = statuses.get(account_id, 200)
        if status != 200:
            return web.json_response({"error": {"message": "temporarily unavailable"}}, status=status)
        return web.json_response(
            {
                "five_hour": {
                    "utilization": 5,
                    "resets_at": (utcnow().replace(tzinfo=timezone.utc) + timedelta(hours=5)).isoformat(),
                }
            }
        )

    app = web.Application()
    app.router.add_get("/api/oauth/usage", usage)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", unused_tcp_port).start()
    monkeypatch.setenv("AGENT_LB_ANTHROPIC_UPSTREAM_BASE_URL", f"http://127.0.0.1:{unused_tcp_port}")
    monkeypatch.setenv("AGENT_LB_USAGE_REFRESH_ENABLED", "true")
    monkeypatch.setenv("AGENT_LB_USAGE_FETCH_MAX_RETRIES", "0")
    monkeypatch.setenv("AGENT_LB_USAGE_REFRESH_INTERVAL_SECONDS", "3600")
    monkeypatch.setenv("AGENT_LB_ACCOUNT_PULSE_RECOVERY_INTERVAL_SECONDS", "900")
    get_settings.cache_clear()
    clock = [time.monotonic()]
    monkeypatch.setattr(updater_module, "time", SimpleNamespace(monotonic=lambda: clock[0], time=time.time))
    updater_module._clear_usage_refresh_state()
    await init_http_client()
    try:
        yield calls, statuses, clock
    finally:
        await updater_module._USAGE_REFRESH_SINGLEFLIGHT.cancel_all()
        updater_module._clear_usage_refresh_state()
        await close_http_client()
        await runner.cleanup()
        get_settings.cache_clear()


async def seed_account(account_id, *, subscription_status="active", deactivation_reason=None):
    now = utcnow()
    encryptor = TokenEncryptor()
    account = Account(
        id=account_id,
        email=f"{account_id}@example.invalid",
        provider="anthropic",
        plan_type="claude",
        access_token_encrypted=encryptor.encrypt(account_id),
        refresh_token_encrypted=encryptor.encrypt("test-refresh"),
        last_refresh=now,
        status=AccountStatus.ACTIVE,
        subscription_status=subscription_status,
        deactivation_reason=deactivation_reason,
        limit_warmup_enabled=False,
    )
    async with SessionLocal() as session:
        session.add(account)
        await session.commit()
        await UsageRepository(session).add_entry(
            account_id,
            100,
            provider="anthropic",
            window="primary",
            reset_at=int(now.replace(tzinfo=timezone.utc).timestamp()) - 10,
            recorded_at=now - timedelta(minutes=1),
            window_minutes=300,
        )
    return account


@pytest.mark.asyncio
async def test_failed_force_refresh_cools_down_without_delaying_healthy_reset(db_setup, usage_endpoint):
    calls, statuses, clock = usage_endpoint
    failing = await seed_account("failing")
    await seed_account("healthy")
    statuses["failing"] = 429
    async with SessionLocal() as session:
        updater = UsageUpdater(UsageRepository(session))
        assert not await updater.force_refresh(failing)
        assert not await updater.force_refresh(failing)
    assert calls["failing"] == 1

    scheduler = UsageRefreshScheduler(interval_seconds=10, enabled=True)
    await scheduler._refresh_once()
    await scheduler._refresh_once()
    assert calls == {"failing": 1, "healthy": 1}
    async with SessionLocal() as session:
        latest = await UsageRepository(session).latest_entry_for_account("healthy", window="primary")
        assert latest is not None
        assert latest.used_percent == 5
        assert latest.reset_at > utcnow().replace(tzinfo=timezone.utc).timestamp()

    clock[0] += 73
    await scheduler._refresh_once()
    assert calls["failing"] == 2
    clock[0] += 73
    await scheduler._refresh_once()
    assert calls["failing"] == 2
    statuses["failing"] = 200
    clock[0] += 73
    await scheduler._refresh_once()
    assert calls == {"failing": 3, "healthy": 1}
    async with SessionLocal() as session:
        updater = UsageUpdater(UsageRepository(session))
        assert await updater.force_refresh(failing)
    assert calls["failing"] == 4


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("subscription_status", "deactivation_reason"),
    [("canceled", None), ("active", ANTHROPIC_OAUTH_ORGANIZATION_BLOCK)],
)
async def test_unusable_subscription_uses_slow_recovery_cadence(
    db_setup, usage_endpoint, subscription_status, deactivation_reason
):
    calls, statuses, clock = usage_endpoint
    await seed_account("unusable", subscription_status=subscription_status, deactivation_reason=deactivation_reason)
    await seed_account("healthy")
    statuses["unusable"] = 429
    scheduler = UsageRefreshScheduler(interval_seconds=10, enabled=True)
    await scheduler._refresh_once()
    await scheduler._refresh_once()
    assert calls == {"unusable": 1, "healthy": 1}
    clock[0] += 899
    await scheduler._refresh_once()
    assert calls["unusable"] == 1
    clock[0] += 2
    await scheduler._refresh_once()
    assert calls == {"unusable": 2, "healthy": 1}
