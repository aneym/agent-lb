from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import pytest
from aiohttp import web
from sqlalchemy import select

from app.core.clients.http import close_http_client, init_http_client
from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, AdditionalUsageHistory, UsageHistory
from app.db.session import SessionLocal
from app.modules.accounts import reset_credit_cache
from app.modules.accounts.auth_manager import AuthManager
from app.modules.accounts.repository import AccountsRepository
from app.modules.accounts.subscription_status import ANTHROPIC_OAUTH_ORGANIZATION_BLOCK
from app.modules.usage import updater as usage_updater
from app.modules.usage.repository import AdditionalUsageRepository, UsageRepository

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@asynccontextmanager
async def usage_server(handler: Callable[[web.Request], Awaitable[web.Response]]) -> AsyncIterator[str]:
    app = web.Application()
    app.router.add_route("GET", "/{tail:.*}", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    try:
        yield f"http://127.0.0.1:{runner.addresses[0][1]}"
    finally:
        await close_http_client()
        await runner.cleanup()


def account(account_id: str, provider: str = "anthropic") -> Account:
    encryptor = TokenEncryptor()
    return Account(
        id=account_id,
        provider=provider,
        email=f"{account_id}@example.invalid",
        plan_type="max" if provider == "anthropic" else "plus",
        access_token_encrypted=encryptor.encrypt("old-access"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
    )


async def configure(monkeypatch: pytest.MonkeyPatch, base_url: str) -> None:
    monkeypatch.setenv("AGENT_LB_ANTHROPIC_UPSTREAM_BASE_URL", base_url)
    monkeypatch.setenv("AGENT_LB_UPSTREAM_BASE_URL", base_url)
    monkeypatch.setenv("AGENT_LB_USAGE_REFRESH_ENABLED", "true")
    monkeypatch.setenv("AGENT_LB_USAGE_FETCH_MAX_RETRIES", "0")
    get_settings.cache_clear()
    usage_updater._clear_usage_refresh_state()
    reset_credit_cache.reset()
    await init_http_client()


async def test_anthropic_http_refresh_persists_provider_snapshot_scoped_quota_and_credits(db_setup, monkeypatch):
    del db_setup
    requests: list[tuple[str, str, str | None]] = []

    async def handler(request: web.Request) -> web.Response:
        requests.append((request.path_qs, request.headers["Authorization"], request.headers.get("Chatgpt-Account-Id")))
        return web.json_response(
            {
                "five_hour": {"utilization": 38, "resets_at": "2030-01-02T00:00:00Z"},
                "seven_day": {"utilization": 70, "resets_at": "2030-01-07T00:00:00Z"},
                "extra_usage": {"is_enabled": True, "monthly_limit": 5000, "used_credits": 1250, "decimal_places": 2},
                "limits": [
                    {
                        "kind": "weekly_scoped",
                        "percent": 94,
                        "resets_at": "2030-01-07T00:00:00Z",
                        "scope": {"model": {"display_name": "Fable"}},
                    }
                ],
                "cedar_ember": {"eligible": True, "grants": [{"id": "launch", "resets_left": 2}]},
            }
        )

    async with usage_server(handler) as base_url:
        await configure(monkeypatch, base_url)
        async with SessionLocal() as session:
            repo = AccountsRepository(session)
            value = account("anthropic-quota")
            value.deactivation_reason = ANTHROPIC_OAUTH_ORGANIZATION_BLOCK
            await repo.upsert(value)
            updater = usage_updater.UsageUpdater(UsageRepository(session), repo, AdditionalUsageRepository(session))
            result = await updater.force_refresh_result(value)
            assert result.fetch_succeeded and result.usage_written
            assert value.plan_type == "claude"
            assert value.deactivation_reason is None
            rows = list((await session.execute(select(UsageHistory))).scalars())
            assert {(row.window, row.used_percent, row.provider) for row in rows} == {
                ("primary", 38.0, "anthropic"),
                ("secondary", 70.0, "anthropic"),
            }
            assert len({row.recorded_at for row in rows}) == 1
            primary = next(row for row in rows if row.window == "primary")
            assert primary.credits_balance == 37.5
            assert primary.credits_has is True
            scoped = (await session.execute(select(AdditionalUsageHistory))).scalar_one()
            assert (scoped.quota_key, scoped.window, scoped.used_percent, scoped.window_minutes) == (
                "anthropic_fable_scoped_weekly",
                "primary",
                94.0,
                10080,
            )
            assert reset_credit_cache.get_count(value.id) == 2
    assert requests == [("/api/oauth/usage?cedar_ember=1", "Bearer old-access", None)]


async def test_provider_retry_uses_anthropic_adapter_after_token_refresh(db_setup, monkeypatch):
    del db_setup
    requests: list[tuple[str, str]] = []

    async def handler(request: web.Request) -> web.Response:
        requests.append((request.path, request.headers["Authorization"]))
        if len(requests) == 1:
            return web.json_response({"error": {"message": "expired"}}, status=401)
        return web.json_response({"five_hour": {"utilization": 12}, "seven_day": {"utilization": 15}})

    async def refresh(self: AuthManager, value: Account, *, force: bool = False) -> Account:
        assert force
        account_id = value.id
        value.access_token_encrypted = TokenEncryptor().encrypt("new-access")
        await self._repo.update_tokens(
            value.id,
            access_token_encrypted=value.access_token_encrypted,
            refresh_token_encrypted=value.refresh_token_encrypted,
            id_token_encrypted=value.id_token_encrypted,
            last_refresh=value.last_refresh,
        )
        refreshed = await self._repo.reload_by_id(account_id)
        assert refreshed is not None
        return refreshed

    monkeypatch.setattr(AuthManager, "ensure_fresh", refresh)
    async with usage_server(handler) as base_url:
        await configure(monkeypatch, base_url)
        async with SessionLocal() as session:
            repo = AccountsRepository(session)
            value = account("anthropic-retry")
            await repo.upsert(value)
            reset_credit_cache.record_count(value.id, 5)
            result = await usage_updater.UsageUpdater(UsageRepository(session), repo).force_refresh_result(value)
            assert result.fetch_succeeded and result.usage_written
            assert reset_credit_cache.get_count(value.id) is None
            assert len((await session.execute(select(UsageHistory))).scalars().all()) == 2
    assert requests == [("/api/oauth/usage", "Bearer old-access"), ("/api/oauth/usage", "Bearer new-access")]


async def test_access_token_override_does_not_refresh_rejected_anthropic_credentials(db_setup, monkeypatch):
    del db_setup
    requests: list[str] = []

    async def handler(request: web.Request) -> web.Response:
        requests.append(request.headers["Authorization"])
        return web.json_response({"error": {"message": "rejected"}}, status=401)

    async def unexpected_refresh(*args, **kwargs):
        raise AssertionError("a rejected override must not rotate stored credentials")

    monkeypatch.setattr(AuthManager, "ensure_fresh", unexpected_refresh)
    async with usage_server(handler) as base_url:
        await configure(monkeypatch, base_url)
        async with SessionLocal() as session:
            repo = AccountsRepository(session)
            value = account("anthropic-override")
            await repo.upsert(value)
            result = await usage_updater.UsageUpdater(UsageRepository(session), repo).force_refresh_result(
                value, access_token_override="probe-access"
            )
            assert not result.fetch_succeeded and not result.usage_written
            assert list((await session.execute(select(UsageHistory))).scalars()) == []
            assert TokenEncryptor().decrypt(value.access_token_encrypted) == "old-access"
    assert requests == ["Bearer probe-access"]


async def test_exhausted_anthropic_window_without_reset_gets_bounded_snapshot(db_setup, monkeypatch):
    del db_setup

    async def handler(request: web.Request) -> web.Response:
        return web.json_response({"five_hour": {"utilization": 100}, "seven_day": {"utilization": 10}})

    async with usage_server(handler) as base_url:
        await configure(monkeypatch, base_url)
        async with SessionLocal() as session:
            value = account("anthropic-bounded")
            repo = AccountsRepository(session)
            await repo.upsert(value)
            before = int(datetime.now(timezone.utc).timestamp())
            assert await usage_updater.UsageUpdater(UsageRepository(session), repo).force_refresh(value)
            primary = await UsageRepository(session).latest_entry_for_account(value.id, window="primary")
            assert primary is not None
            assert before + 18000 <= primary.reset_at <= before + 18005


async def test_openai_refresh_keeps_its_endpoint_and_upstream_reset_credit_count(db_setup, monkeypatch):
    del db_setup
    requests: list[tuple[str, str | None]] = []

    async def handler(request: web.Request) -> web.Response:
        requests.append((request.path, request.headers.get("Chatgpt-Account-Id")))
        return web.json_response(
            {
                "plan_type": "pro",
                "rate_limit": {
                    "primary_window": {"used_percent": 20, "limit_window_seconds": 18000},
                    "secondary_window": {"used_percent": 30, "limit_window_seconds": 604800},
                },
                "rate_limit_reset_credits": {"available_count": 3},
            }
        )

    async with usage_server(handler) as base_url:
        await configure(monkeypatch, base_url)
        async with SessionLocal() as session:
            repo = AccountsRepository(session)
            value = account("codex-quota", "openai")
            value.chatgpt_account_id = "codex-identity"
            await repo.upsert(value)
            assert await usage_updater.UsageUpdater(UsageRepository(session), repo).force_refresh(value)
            assert value.plan_type == "pro"
            assert reset_credit_cache.get_count(value.id) == 3
            assert {row.provider for row in (await session.execute(select(UsageHistory))).scalars()} == {"openai"}
    assert requests == [("/backend-api/wham/usage", "codex-identity")]


async def test_anthropic_rate_limit_cooldown_prevents_poll_storm_and_success_clears_it(db_setup, monkeypatch):
    del db_setup
    requests = 0

    async def handler(request: web.Request) -> web.Response:
        nonlocal requests
        requests += 1
        if requests == 1:
            return web.json_response({"error": {"message": "slow down"}}, status=429)
        return web.json_response({"five_hour": {"utilization": 20}, "seven_day": {"utilization": 25}})

    async with usage_server(handler) as base_url:
        await configure(monkeypatch, base_url)
        async with SessionLocal() as session:
            value = account("anthropic-poll-backoff")
            repo = AccountsRepository(session)
            await repo.upsert(value)
            updater = usage_updater.UsageUpdater(UsageRepository(session), repo)
            result = await updater.force_refresh_result(value)
            assert not result.fetch_succeeded
            assert not await updater.refresh_accounts([value], {})
            assert requests == 1
            assert await updater.force_refresh(value)
            assert value.id not in usage_updater.usage_refresh_rate_limit_strikes
            assert value.id not in usage_updater._usage_refresh_auth_cooldowns
    assert requests == 2


async def test_usage_metadata_sync_does_not_restore_peer_rotated_credentials(db_setup, monkeypatch):
    del db_setup
    rotated = TokenEncryptor().encrypt("peer-rotated-refresh")
    rotated_access = TokenEncryptor().encrypt("peer-rotated-access")

    async def handler(request: web.Request) -> web.Response:
        async with SessionLocal() as peer_session:
            peer_repo = AccountsRepository(peer_session)
            peer = await peer_repo.get_by_id("anthropic-peer-rotate")
            assert peer is not None
            assert await peer_repo.update_tokens(
                peer.id,
                access_token_encrypted=rotated_access,
                refresh_token_encrypted=rotated,
                id_token_encrypted=peer.id_token_encrypted,
                last_refresh=utcnow(),
            )
        return web.json_response({"five_hour": {"utilization": 12}, "seven_day": {"utilization": 15}})

    async with usage_server(handler) as base_url:
        await configure(monkeypatch, base_url)
        async with SessionLocal() as session:
            repo = AccountsRepository(session)
            value = account("anthropic-peer-rotate")
            await repo.upsert(value)
            assert await usage_updater.UsageUpdater(UsageRepository(session), repo).force_refresh(value)
        async with SessionLocal() as session:
            saved = await AccountsRepository(session).get_by_id("anthropic-peer-rotate")
            assert saved is not None
            assert saved.plan_type == "claude"
            assert saved.access_token_encrypted == rotated_access
            assert saved.refresh_token_encrypted == rotated


async def test_failed_usage_does_not_overwrite_peer_pause(db_setup, monkeypatch):
    del db_setup

    async def handler(request: web.Request) -> web.Response:
        async with SessionLocal() as peer_session:
            await AccountsRepository(peer_session).update_status(
                "anthropic-peer-pause", AccountStatus.PAUSED, "operator pause"
            )
        return web.json_response({"error": {"message": "not found"}}, status=404)

    async with usage_server(handler) as base_url:
        await configure(monkeypatch, base_url)
        async with SessionLocal() as session:
            repo = AccountsRepository(session)
            value = account("anthropic-peer-pause")
            await repo.upsert(value)
            result = await usage_updater.UsageUpdater(UsageRepository(session), repo).force_refresh_result(value)
            assert not result.fetch_succeeded
        async with SessionLocal() as session:
            saved = await AccountsRepository(session).get_by_id("anthropic-peer-pause")
            assert saved is not None
            assert saved.status == AccountStatus.PAUSED
            assert saved.deactivation_reason == "operator pause"
