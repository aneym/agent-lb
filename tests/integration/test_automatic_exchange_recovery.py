from __future__ import annotations

from datetime import timedelta
from hashlib import sha256

import pytest
from aiohttp import web

from app.core.auth.refresh import RefreshError
from app.core.clients.http import close_http_client, init_http_client
from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.core.usage.refresh_scheduler import UsageRefreshScheduler
from app.core.utils.time import utcnow
from app.db.models import Account, AccountExchangeIntent, AccountStatus, UsageHistory
from app.db.session import SessionLocal
from app.modules.accounts.auth_manager import AuthManager
from app.modules.accounts.repository import AccountsRepository

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "entrypoint,provider", [("auth", "anthropic"), ("scheduler", "anthropic"), ("scheduler", "kimi")]
)
@pytest.mark.parametrize("outcome", ["success", "ambiguous", "permanent"])
async def test_eligible_exchange_recovers_without_reset(db_setup, monkeypatch, entrypoint, provider, outcome):

    requests = []

    async def token(request):
        requests.append(
            await request.json() if request.content_type == "application/json" else dict(await request.post())
        )
        if outcome == "ambiguous":
            return web.json_response({"error": "server_error"}, status=503)
        if outcome == "permanent":
            return web.json_response({"error": "invalid_grant"}, status=400)
        return web.json_response({"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 3600})

    app = web.Application()
    app.router.add_post("/token", token)

    async def usage(request):
        return web.json_response({"five_hour": {"utilization": 20}, "seven_day": {"utilization": 20}})

    app.router.add_get("/api/oauth/usage", usage)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    monkeypatch.setenv("AGENT_LB_ANTHROPIC_OAUTH_TOKEN_URL", f"http://127.0.0.1:{port}/token")
    monkeypatch.setenv("AGENT_LB_ANTHROPIC_UPSTREAM_BASE_URL", f"http://127.0.0.1:{port}")
    monkeypatch.setenv("AGENT_LB_KIMI_OAUTH_TOKEN_URL", f"http://127.0.0.1:{port}/token")
    monkeypatch.setenv("AGENT_LB_USAGE_REFRESH_ENABLED", "true")
    get_settings.cache_clear()
    encryptor = TokenEncryptor()
    account_id = f"automatic-{entrypoint}-{provider}-{outcome}"
    async with SessionLocal() as session:
        await AccountsRepository(session).upsert(
            Account(
                id=account_id,
                provider=provider,
                email="recovery@example.invalid",
                plan_type="claude",
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
                started_at=utcnow() - timedelta(hours=1),
                replay=False,
            )
        )

        session.add(
            UsageHistory(
                account_id=account_id,
                provider="anthropic",
                window="primary",
                used_percent=20,
                recorded_at=utcnow(),
                reset_at=int((utcnow() + timedelta(seconds=120)).timestamp()),
            )
        )
        await session.commit()
    await init_http_client()
    try:
        for _ in range(2):
            if entrypoint == "scheduler":
                await UsageRefreshScheduler(interval_seconds=10, enabled=True)._refresh_once()
            else:
                async with SessionLocal() as session:
                    repo = AccountsRepository(session)
                    account = await repo.get_by_id(account_id)
                    try:
                        await AuthManager(repo).ensure_fresh(account)
                    except RefreshError as exc:
                        assert outcome != "success"
                        assert exc.code == ("exchange_uncertain" if outcome == "ambiguous" else "invalid_grant")
            async with SessionLocal() as session:
                stored = await session.get(Account, account_id)
                intent = await session.get(AccountExchangeIntent, account_id)
                if entrypoint == "auth":
                    assert stored.status == AccountStatus.EXCHANGE_UNCERTAIN
                    assert intent is not None and intent.replay is False
                    continue
                expected = {
                    "success": AccountStatus.ACTIVE,
                    "ambiguous": AccountStatus.EXCHANGE_UNCERTAIN,
                    "permanent": AccountStatus.DEACTIVATED if provider == "kimi" else AccountStatus.REAUTH_REQUIRED,
                }[outcome]
                assert stored.status == expected
                if outcome == "success":
                    assert encryptor.decrypt(stored.refresh_token_encrypted) == "new-refresh"
                    assert intent is None
                elif outcome == "ambiguous":
                    assert intent.replay is True
                else:
                    assert intent is None
        if entrypoint == "auth":
            assert requests == []
        else:
            assert len(requests) == 1
            assert requests[0]["refresh_token"] == "refresh"
    finally:
        await close_http_client()
        await runner.cleanup()
        get_settings.cache_clear()


@pytest.mark.asyncio
@pytest.mark.parametrize("guard", ["replay", "unsaved", "recent", "missing", "mismatch", "mirror", "permanent"])
async def test_automatic_recovery_preserves_closed_exchange_guards(db_setup, monkeypatch, guard):

    requests = []

    async def forbidden(request):
        requests.append(await request.json())
        return web.json_response({"access_token": "wrong", "refresh_token": "wrong"})

    app = web.Application()
    app.router.add_post("/token", forbidden)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    monkeypatch.setenv("AGENT_LB_ANTHROPIC_OAUTH_TOKEN_URL", f"http://127.0.0.1:{port}/token")
    get_settings.cache_clear()
    encryptor = TokenEncryptor()
    account_id = f"guard-{guard}"
    status = AccountStatus.REAUTH_REQUIRED if guard == "permanent" else AccountStatus.EXCHANGE_UNCERTAIN
    async with SessionLocal() as session:
        await AccountsRepository(session).upsert(
            Account(
                id=account_id,
                provider="anthropic",
                email=f"{account_id}@example.invalid",
                plan_type="claude",
                access_token_encrypted=encryptor.encrypt("access"),
                refresh_token_encrypted=encryptor.encrypt("refresh"),
                last_refresh=utcnow(),
                access_expires_at=utcnow() + timedelta(hours=1),
                status=status,
                owner_instance="remote-test-instance" if guard == "mirror" else None,
            )
        )
        if guard != "missing":
            session.add(
                AccountExchangeIntent(
                    account_id=account_id,
                    refresh_token_sha256=sha256(b"other" if guard == "mismatch" else b"refresh").hexdigest(),
                    started_at=utcnow() if guard == "recent" else utcnow() - timedelta(hours=1),
                    replay=guard == "replay",
                    reason="unsaved" if guard == "unsaved" else None,
                )
            )
            await session.commit()
    await init_http_client()
    try:
        async with SessionLocal() as session:
            repo = AccountsRepository(session)
            account = await repo.get_by_id(account_id)

            if guard == "permanent":
                assert not await repo.begin_exchange(
                    account_id,
                    sha256(b"refresh").hexdigest(),
                    account.refresh_token_encrypted,
                    replay=True,
                    timeout_seconds=30,
                )
            else:
                assert (await AuthManager(repo).ensure_fresh(account)).status == status
        assert requests == []
        async with SessionLocal() as session:
            assert (await session.get(Account, account_id)).status == status
            intent = await session.get(AccountExchangeIntent, account_id)
            assert (intent is None) == (guard == "missing")
    finally:
        await close_http_client()
        await runner.cleanup()
        get_settings.cache_clear()
