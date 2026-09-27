from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
from unittest.mock import patch

import aiohttp
import pytest
from sqlalchemy import text

from app.core.anthropic import oauth as anthropic_oauth
from app.core.auth import refresh as openai_refresh
from app.core.auth.exchange_phase import ExchangePhase
from app.core.auth.refresh import RefreshError, TokenRefreshResult
from app.core.clients.codex import CodexTransportError
from app.core.crypto import TokenEncryptor
from app.core.providers import kimi as kimi_provider
from app.core.utils.time import utcnow
from app.db.models import Account, AccountExchangeIntent, AccountStatus
from app.db.session import SessionLocal
from app.modules.accounts import auth_manager as auth_manager_module
from app.modules.accounts.auth_manager import AuthManager
from app.modules.accounts.repository import AccountsRepository

pytestmark = pytest.mark.integration


async def _account(account_id: str) -> None:
    encryptor = TokenEncryptor()
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
                status=AccountStatus.ACTIVE,
            )
        )


async def _refresh(account_id: str) -> Account:
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        account = await repo.get_by_id(account_id)
        assert account is not None
        return await AuthManager(repo).refresh_account(account)


@pytest.mark.asyncio
async def test_pre_send_releases_intent_then_retries(db_setup, monkeypatch):
    await _account("pre-send")
    calls = 0

    async def transport(self, refresh_token, **kwargs):
        nonlocal calls
        calls += 1
        kwargs["on_exchange_start"]()
        if calls == 1:
            raise RefreshError("transport_error", "connect refused", False, phase=ExchangePhase.PRE_SEND)
        return TokenRefreshResult("new-access", "new-refresh", None, None, None, None)

    monkeypatch.setattr(AuthManager, "_refresh_tokens", transport)
    with pytest.raises(RefreshError) as error:
        await _refresh("pre-send")
    assert error.value.code == "transport_error"
    async with SessionLocal() as session:
        assert await session.get(AccountExchangeIntent, "pre-send") is None
        assert (await session.get(Account, "pre-send")).status == AccountStatus.ACTIVE
    assert (await _refresh("pre-send")).status == AccountStatus.ACTIVE
    assert calls == 2


@pytest.mark.asyncio
async def test_ambiguous_waits_then_replays_once_with_database_clock(db_setup, monkeypatch):
    await _account("replay")
    calls = 0

    async def transport(self, refresh_token, **kwargs):
        nonlocal calls
        calls += 1
        kwargs["on_exchange_start"]()
        if calls == 1:
            raise RefreshError("transport_error", "read timed out", False)
        return TokenRefreshResult("new-access", "new-refresh", None, None, None, None)

    monkeypatch.setattr(AuthManager, "_refresh_tokens", transport)
    with pytest.raises(RefreshError, match="uncertain"):
        await _refresh("replay")
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        account = await repo.get_by_id("replay")
        assert account.status == AccountStatus.EXCHANGE_UNCERTAIN
        assert (await AuthManager(repo).ensure_fresh(account)).status == AccountStatus.EXCHANGE_UNCERTAIN
    with pytest.raises(RefreshError, match="uncertain"):
        await _refresh("replay")
    assert calls == 1
    async with SessionLocal() as session:
        intent = await session.get(AccountExchangeIntent, "replay")
        intent.started_at = utcnow() - timedelta(seconds=1000)
        await session.commit()
    with patch("app.modules.accounts.repository.utcnow", return_value=utcnow() - timedelta(days=30)):
        assert (await _refresh("replay")).status == AccountStatus.ACTIVE
    assert calls == 2
    async with SessionLocal() as session:
        assert await session.get(AccountExchangeIntent, "replay") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["reused", "ambiguous", "unsaved"])
async def test_replay_outcomes_never_loop(db_setup, monkeypatch, outcome):
    await _account(f"outcome-{outcome}")
    account_id = f"outcome-{outcome}"
    calls = 0

    async def transport(self, refresh_token, **kwargs):
        nonlocal calls
        calls += 1
        kwargs["on_exchange_start"]()
        if calls == 1:
            raise RefreshError("transport_error", "read timed out", False)
        if outcome == "reused":
            raise RefreshError("refresh_token_reused", "reused", True, phase=ExchangePhase.ANSWERED)
        if outcome == "unsaved":
            return TokenRefreshResult("new-access", "new-refresh", None, None, None, None)
        raise RefreshError("transport_error", "read timed out", False)

    monkeypatch.setattr(AuthManager, "_refresh_tokens", transport)
    with pytest.raises(RefreshError):
        await _refresh(account_id)
    async with SessionLocal() as session:
        intent = await session.get(AccountExchangeIntent, account_id)
        intent.started_at = utcnow() - timedelta(seconds=1000)
        await session.commit()
    if outcome == "unsaved":

        async def cannot_persist(self, *args, **kwargs):
            raise RuntimeError("database unavailable")

        monkeypatch.setattr(AccountsRepository, "update_tokens", cannot_persist)
    with pytest.raises(RefreshError):
        await _refresh(account_id)
    async with SessionLocal() as session:
        account = await session.get(Account, account_id)
        intent = await session.get(AccountExchangeIntent, account_id)
        if outcome == "reused":
            assert account.status == AccountStatus.REAUTH_REQUIRED
            assert intent is None
        else:
            assert account.status == AccountStatus.EXCHANGE_UNCERTAIN
            assert intent.replay is True
            assert intent.reason == ("unsaved" if outcome == "unsaved" else None)
    if outcome != "reused":
        with pytest.raises(RefreshError, match="uncertain"):
            await _refresh(account_id)
        assert calls == 2


@pytest.mark.asyncio
async def test_late_uncertain_update_cannot_mark_rotated_token(db_setup):
    await _account("late-writer")
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        account = await repo.get_by_id("late-writer")
        original = account.refresh_token_encrypted
        from app.modules.accounts.auth_manager import _refresh_token_material_fingerprint

        token_hash = _refresh_token_material_fingerprint(encryptor, original)
        assert await repo.begin_exchange(account.id, token_hash, original)
        await session.execute(
            text("UPDATE accounts SET refresh_token_encrypted = :rotated WHERE id = :id"),
            {"rotated": encryptor.encrypt("rotated"), "id": account.id},
        )
        await session.commit()
        await repo.mark_exchange_uncertain(account.id, token_hash, original, reason="unsaved")
        assert (await repo.reload_by_id(account.id)).status == AccountStatus.ACTIVE
        assert (await session.get(AccountExchangeIntent, account.id)).reason is None


@pytest.mark.asyncio
async def test_answered_5xx_with_provider_code_keeps_intent(db_setup, monkeypatch):
    await _account("answered-5xx")

    async def transport(self, refresh_token, **kwargs):
        kwargs["on_exchange_start"]()
        raise RefreshError(
            "server_busy", "temporarily unavailable", False, phase=ExchangePhase.ANSWERED, status_code=503
        )

    monkeypatch.setattr(AuthManager, "_refresh_tokens", transport)
    with pytest.raises(RefreshError, match="uncertain"):
        await _refresh("answered-5xx")
    async with SessionLocal() as session:
        assert (await session.get(Account, "answered-5xx")).status == AccountStatus.EXCHANGE_UNCERTAIN
        assert await session.get(AccountExchangeIntent, "answered-5xx") is not None


@pytest.mark.asyncio
async def test_replay_replacement_requires_window_inside_transaction(db_setup):
    await _account("replay-window")
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        account = await repo.get_by_id("replay-window")
        account_id = account.id
        original = account.refresh_token_encrypted
        from app.modules.accounts.auth_manager import _refresh_token_material_fingerprint

        token_hash = _refresh_token_material_fingerprint(encryptor, original)
        assert await repo.begin_exchange(account_id, token_hash, original)
        assert not await repo.begin_exchange(account_id, token_hash, original, replay=True, timeout_seconds=30)
        intent = await session.get(AccountExchangeIntent, account_id)
        assert intent.replay is False
        intent.started_at = utcnow() - timedelta(seconds=1000)
        await session.commit()
        assert await repo.begin_exchange(account_id, token_hash, original, replay=True, timeout_seconds=30)
        assert (await session.get(AccountExchangeIntent, account_id)).replay is True


@pytest.mark.asyncio
async def test_identity_sync_preserves_access_expiry(db_setup):
    await _account("metadata")
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        account = await repo.get_by_id("metadata")
        expiry = account.access_expires_at
        assert await repo.update_tokens(
            account.id,
            account.access_token_encrypted,
            account.refresh_token_encrypted,
            account.id_token_encrypted,
            account.last_refresh,
            chatgpt_account_id="metadata-id",
        )
        assert (await repo.reload_by_id(account.id)).access_expires_at == expiry


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "anthropic", "kimi"])
async def test_truncated_success_is_unsaved_and_never_auto_replayed(db_setup, monkeypatch, provider: str):
    account_id = f"truncated-{provider}"
    await _account(account_id)
    if provider != "anthropic":
        async with SessionLocal() as session:
            account = await session.get(Account, account_id)
            account.provider = provider
            if provider == "openai":
                account.chatgpt_account_id = "workspace-truncated"
            await session.commit()

    calls = 0

    async def respond(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        nonlocal calls
        await reader.read(65536)
        calls += 1
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 400\r\n\r\n"
            b'{"access_token":"incomplete"'
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(respond, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async with aiohttp.ClientSession() as client:

        @asynccontextmanager
        async def leased_session(_session=None):
            yield client

        transport = {
            "openai": openai_refresh,
            "anthropic": anthropic_oauth,
            "kimi": kimi_provider,
        }[provider]
        monkeypatch.setattr(transport, "lease_http_session", leased_session)
        monkeypatch.setattr(auth_manager_module, "resolve_upstream_route", _direct_route)
        if provider == "openai":
            monkeypatch.setenv("AGENT_LB_AUTH_BASE_URL", f"http://127.0.0.1:{port}")
            from app.core.config.settings import get_settings

            get_settings.cache_clear()
        else:
            url_setting = (
                "AGENT_LB_ANTHROPIC_OAUTH_TOKEN_URL" if provider == "anthropic" else "AGENT_LB_KIMI_OAUTH_TOKEN_URL"
            )
            monkeypatch.setenv(url_setting, f"http://127.0.0.1:{port}/oauth/token")
            from app.core.config.settings import get_settings

            get_settings.cache_clear()
        try:
            with pytest.raises(RefreshError) as error:
                await _refresh(account_id)
            assert error.value.code == "exchange_uncertain"
            async with SessionLocal() as session:
                account = await session.get(Account, account_id)
                intent = await session.get(AccountExchangeIntent, account_id)
                assert account.status == AccountStatus.EXCHANGE_UNCERTAIN
                assert intent.reason == "unsaved"
                intent.started_at = utcnow() - timedelta(hours=1)
                await session.commit()
            with pytest.raises(RefreshError, match="uncertain"):
                await _refresh(account_id)
            assert calls == 1
        finally:
            server.close()
            await server.wait_closed()
            get_settings.cache_clear()


async def _direct_route(*args, **kwargs):
    return None


@pytest.mark.asyncio
async def test_routed_invalid_success_response_is_unsaved(db_setup, monkeypatch):
    from app.core.auth import refresh as openai_refresh

    account_id = "routed-invalid-response"
    await _account(account_id)
    async with SessionLocal() as session:
        account = await session.get(Account, account_id)
        account.provider = "openai"
        await session.commit()

    class _InvalidResponse:
        status_code = 200

        def json(self):
            raise ValueError("unreadable")

    class _CodexClient:
        def __init__(self):
            self.calls = 0

        async def request(self, *args, **kwargs):
            self.calls += 1
            return _InvalidResponse()

        async def close(self):
            pass

    client = _CodexClient()

    async def routed(*args, **kwargs):
        return object()

    @asynccontextmanager
    async def leased_session():
        yield object()

    monkeypatch.setattr(auth_manager_module, "resolve_upstream_route", routed)
    monkeypatch.setattr(openai_refresh, "lease_http_session", leased_session)
    monkeypatch.setattr(openai_refresh, "CodexClient", lambda session: client)
    monkeypatch.setattr(openai_refresh, "create_codex_session", lambda: object())
    monkeypatch.setattr(openai_refresh, "require_route_or_direct_egress_opt_in", lambda **kwargs: None)
    with pytest.raises(RefreshError, match="uncertain") as error:
        await _refresh(account_id)
    assert isinstance(error.value.__cause__, RefreshError), repr(error.value.__cause__)
    assert error.value.__cause__.code == "invalid_response"
    async with SessionLocal() as session:
        assert (await session.get(AccountExchangeIntent, account_id)).reason == "unsaved"
        assert (await session.get(Account, account_id)).status == AccountStatus.EXCHANGE_UNCERTAIN
    assert client.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [200, 503])
async def test_routed_unreadable_status_retains_exchange_phase(db_setup, monkeypatch, status: int):
    account_id = f"routed-unreadable-{status}"
    await _account(account_id)
    async with SessionLocal() as session:
        account = await session.get(Account, account_id)
        account.provider = "openai"
        await session.commit()

    class _CodexClient:
        calls = 0

        async def request(self, *args, **kwargs):
            self.calls += 1
            raise CodexTransportError("response unreadable", status_code=status)

        async def close(self):
            pass

    client = _CodexClient()

    async def routed(*args, **kwargs):
        return object()

    monkeypatch.setattr(auth_manager_module, "resolve_upstream_route", routed)
    monkeypatch.setattr(openai_refresh, "CodexClient", lambda session: client)
    monkeypatch.setattr(openai_refresh, "create_codex_session", lambda: object())
    monkeypatch.setattr(openai_refresh, "require_route_or_direct_egress_opt_in", lambda **kwargs: None)
    with pytest.raises(RefreshError) as error:
        await _refresh(account_id)
    async with SessionLocal() as session:
        intent = await session.get(AccountExchangeIntent, account_id)
        account = await session.get(Account, account_id)
        if status == 200:
            assert error.value.code == "exchange_uncertain"
            assert isinstance(error.value.__cause__, RefreshError)
            assert error.value.__cause__.code == "invalid_response"
            assert intent.reason == "unsaved"
            assert account.status == AccountStatus.EXCHANGE_UNCERTAIN
        else:
            assert error.value.code == "exchange_uncertain"
            assert intent.reason is None
            assert account.status == AccountStatus.EXCHANGE_UNCERTAIN
    assert client.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "anthropic", "kimi"])
async def test_lease_failure_before_send_clears_intent(db_setup, monkeypatch, provider: str):
    account_id = f"lease-failure-{provider}"
    await _account(account_id)
    if provider != "anthropic":
        async with SessionLocal() as session:
            account = await session.get(Account, account_id)
            account.provider = provider
            await session.commit()
        monkeypatch.setattr(auth_manager_module, "resolve_upstream_route", _direct_route)

    @asynccontextmanager
    async def unavailable_session(_session=None):
        raise RuntimeError("HTTP client not initialized")
        yield

    transport = {
        "openai": openai_refresh,
        "anthropic": anthropic_oauth,
        "kimi": kimi_provider,
    }[provider]
    monkeypatch.setattr(transport, "lease_http_session", unavailable_session)
    with pytest.raises(RefreshError) as error:
        await _refresh(account_id)
    assert error.value.code == "transport_error"
    assert error.value.phase == ExchangePhase.PRE_SEND
    async with SessionLocal() as session:
        assert await session.get(AccountExchangeIntent, account_id) is None
        assert (await session.get(Account, account_id)).status == AccountStatus.ACTIVE
