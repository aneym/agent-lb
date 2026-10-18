from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta

import aiohttp
import pytest

from app.core.anthropic import oauth as anthropic_oauth
from app.core.auth import refresh as openai_refresh
from app.core.auth.exchange_phase import ExchangePhase
from app.core.auth.refresh import RefreshError
from app.core.clients.codex import CodexTransportError
from app.core.providers import kimi as kimi_provider
from app.core.utils.time import utcnow
from app.db.models import Account, AccountExchangeIntent, AccountStatus
from app.db.session import SessionLocal
from app.modules.accounts import auth_manager as auth_manager_module
from tests.integration.exchange_intent_support import (
    _account,
    _direct_route,
    _refresh,
)

pytestmark = pytest.mark.integration


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
