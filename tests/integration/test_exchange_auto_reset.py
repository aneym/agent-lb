from __future__ import annotations

from datetime import timedelta
from hashlib import sha256

import pytest
from aiohttp import web

from app.core.clients.http import close_http_client, init_http_client
from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.core.usage.refresh_scheduler import UsageRefreshScheduler
from app.core.utils.time import utcnow
from app.db.models import Account, AccountExchangeIntent, AccountStatus, UsageHistory
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "prior,outcome,provider,attempt,recent",
    [
        (prior, outcome, "anthropic", 0, False)
        for prior in ("replay", "unsaved")
        for outcome in ("success", "invalid_grant", "503")
    ]
    + [
        ("replay", "exhausted", "anthropic", 3, False),
        ("unsaved", "exhausted", "anthropic", 3, False),
        ("unsaved", "excluded", "openai", 0, False),
        ("replay", "recent", "anthropic", 0, True),
    ],
)
async def test_scheduler_auto_resets_exchange(db_setup, monkeypatch, prior, outcome, provider, attempt, recent):
    requests = []
    usage_requests = []

    async def token(request):
        requests.append(
            await request.json() if request.content_type == "application/json" else dict(await request.post())
        )
        if outcome == "503":
            return web.json_response({"error": "server_error"}, status=503)
        if outcome == "invalid_grant":
            return web.json_response({"error": "invalid_grant"}, status=400)
        return web.json_response({"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 3600})

    async def usage(request):
        usage_requests.append(request.path)
        return web.json_response({"five_hour": {"utilization": 20}, "seven_day": {"utilization": 20}})

    app = web.Application()
    app.router.add_post("/token", token)
    app.router.add_get("/api/oauth/usage", usage)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    monkeypatch.setenv("AGENT_LB_ANTHROPIC_OAUTH_TOKEN_URL", f"http://127.0.0.1:{port}/token")
    monkeypatch.setenv("AGENT_LB_ANTHROPIC_UPSTREAM_BASE_URL", f"http://127.0.0.1:{port}")
    monkeypatch.setenv("AGENT_LB_OPENAI_OAUTH_TOKEN_URL", f"http://127.0.0.1:{port}/token")
    monkeypatch.setenv("AGENT_LB_USAGE_REFRESH_ENABLED", "true")
    get_settings.cache_clear()
    encryptor = TokenEncryptor()
    account_id = f"auto-reset-{prior}-{outcome}"
    async with SessionLocal() as session:
        await AccountsRepository(session).upsert(
            Account(
                id=account_id,
                provider=provider,
                email=f"{account_id}@example.invalid",
                plan_type="claude" if provider == "anthropic" else "plus",
                access_token_encrypted=encryptor.encrypt("access"),
                refresh_token_encrypted=encryptor.encrypt("refresh"),
                last_refresh=utcnow(),
                access_expires_at=utcnow() + timedelta(hours=1),
                status=AccountStatus.EXCHANGE_UNCERTAIN,
            )
        )
        session.add(
            AccountExchangeIntent(
                account_id=account_id,
                refresh_token_sha256=sha256(b"refresh").hexdigest(),
                started_at=utcnow() if recent else utcnow() - timedelta(hours=2),
                replay=prior == "replay",
                reason="unsaved" if prior == "unsaved" else None,
                auto_resets=attempt,
            )
        )
        session.add(
            UsageHistory(
                account_id=account_id,
                provider=provider,
                window="primary",
                used_percent=20,
                recorded_at=utcnow() - timedelta(minutes=5),
                reset_at=int((utcnow() + timedelta(seconds=120)).timestamp()),
            )
        )
        await session.commit()
    await init_http_client()
    try:
        scheduler = UsageRefreshScheduler(interval_seconds=10, enabled=True)
        for _ in range(2):
            await scheduler._refresh_once()
            async with SessionLocal() as session:
                stored = await session.get(Account, account_id)
                intent = await session.get(AccountExchangeIntent, account_id)
                if outcome == "success":
                    assert stored.status == AccountStatus.ACTIVE
                    assert encryptor.decrypt(stored.refresh_token_encrypted) == "new-refresh"
                    assert intent is None
                elif outcome == "invalid_grant":
                    assert stored.status == AccountStatus.REAUTH_REQUIRED
                    assert intent is None
                else:
                    assert stored.status == AccountStatus.EXCHANGE_UNCERTAIN
                    assert intent is not None
                    if outcome == "503":
                        assert intent.replay is True
                        assert intent.auto_resets == 1
                    elif outcome == "exhausted":
                        assert intent.reason == "auto_exhausted"
                        assert intent.auto_resets == 3
                    else:
                        assert intent.auto_resets == 0
            if outcome in {"success", "invalid_grant", "503"}:
                assert len(requests) == 1
                assert requests[0]["refresh_token"] == "refresh"
            else:
                assert requests == []
        if outcome == "success":
            assert usage_requests
    finally:
        await close_http_client()
        await runner.cleanup()
        get_settings.cache_clear()
