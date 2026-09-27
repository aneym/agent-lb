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
from app.db.session import SessionLocal, get_background_session
from app.modules.accounts import auth_manager as auth_manager_module
from app.modules.accounts.auth_manager import AuthManager, _refresh_token_material_fingerprint
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


async def _refresh_in_background_session(account_id: str) -> Account:
    # Production lifecycle: get_background_session rolls back an open
    # transaction before closing, which expires every row it loaded.
    async with get_background_session() as session:
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


@pytest.mark.asyncio
async def test_stale_intent_after_tolerant_relogin_refreshes(db_setup, monkeypatch):
    account_id = "stale-after-relogin"
    await _account(account_id)
    calls = 0

    async def transport(self, refresh_token, **kwargs):
        nonlocal calls
        calls += 1
        kwargs["on_exchange_start"]()
        if calls == 1:
            raise RefreshError("transport_error", "read timed out", False)
        assert refresh_token == "tolerant-refresh"
        return TokenRefreshResult("new-access", "si-refresh", None, None, None, None)

    monkeypatch.setattr(AuthManager, "_refresh_tokens", transport)
    with pytest.raises(RefreshError, match="uncertain"):
        await _refresh(account_id)
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        intent = await session.get(AccountExchangeIntent, account_id)
        assert intent is not None
        await session.execute(
            text("UPDATE accounts SET refresh_token_encrypted = :token, status = 'active' WHERE id = :id"),
            {"token": encryptor.encrypt("tolerant-refresh"), "id": account_id},
        )
        await session.commit()
    refreshed = await _refresh(account_id)
    assert refreshed.status == AccountStatus.ACTIVE
    assert encryptor.decrypt(refreshed.refresh_token_encrypted) == "si-refresh"
    assert calls == 2  # exactly one SI call after the tolerant rewrite
    async with SessionLocal() as session:
        assert await session.get(AccountExchangeIntent, account_id) is None


@pytest.mark.asyncio
async def test_stale_crash_intent_after_tolerant_rotation_refreshes(db_setup, monkeypatch):
    account_id = "stale-crash"
    await _account(account_id)
    encryptor = TokenEncryptor()
    from app.modules.accounts.auth_manager import _refresh_token_material_fingerprint

    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        account = await repo.get_by_id(account_id)
        assert await repo.begin_exchange(
            account_id,
            _refresh_token_material_fingerprint(encryptor, account.refresh_token_encrypted),
            account.refresh_token_encrypted,
        )
        await session.execute(
            text("UPDATE accounts SET refresh_token_encrypted = :token WHERE id = :id"),
            {"token": encryptor.encrypt("tolerant-rotated"), "id": account_id},
        )
        await session.commit()
    calls = 0

    async def transport(self, refresh_token, **kwargs):
        nonlocal calls
        calls += 1
        assert refresh_token == "tolerant-rotated"
        kwargs["on_exchange_start"]()
        return TokenRefreshResult("new-access", "si-rotated", None, None, None, None)

    monkeypatch.setattr(AuthManager, "_refresh_tokens", transport)
    refreshed = await _refresh(account_id)
    assert calls == 1
    assert encryptor.decrypt(refreshed.refresh_token_encrypted) == "si-rotated"
    async with SessionLocal() as session:
        assert await session.get(AccountExchangeIntent, account_id) is None


@pytest.mark.asyncio
async def test_uncertain_mismatched_intent_needs_operator_reset(db_setup, monkeypatch):
    account_id = "uncertain-mismatch"
    await _account(account_id)
    encryptor = TokenEncryptor()
    from app.modules.accounts.auth_manager import _refresh_token_material_fingerprint

    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        account = await repo.get_by_id(account_id)
        original = account.refresh_token_encrypted
        fingerprint = _refresh_token_material_fingerprint(encryptor, original)
        assert await repo.begin_exchange(account_id, fingerprint, original)
        await repo.mark_exchange_uncertain(account_id, fingerprint, original)
        await session.execute(
            text("UPDATE accounts SET refresh_token_encrypted = :token WHERE id = :id"),
            {"token": encryptor.encrypt("different-refresh"), "id": account_id},
        )
        await session.commit()
    calls = 0

    async def transport(self, refresh_token, **kwargs):
        nonlocal calls
        calls += 1
        kwargs["on_exchange_start"]()
        return TokenRefreshResult("new-access", "si-refresh", None, None, None, None)

    monkeypatch.setattr(AuthManager, "_refresh_tokens", transport)
    with pytest.raises(RefreshError) as error:
        await _refresh(account_id)
    assert error.value.code == "exchange_intent_conflict"
    assert calls == 0
    async with SessionLocal() as session:
        intent = await session.get(AccountExchangeIntent, account_id)
        assert intent is not None
        intent.started_at = utcnow() - timedelta(seconds=1000)
        await session.commit()
        assert await AccountsRepository(session).clear_exchange_intent(account_id, 30) is True
    assert (await _refresh(account_id)).status == AccountStatus.ACTIVE
    assert calls == 1
    async with SessionLocal() as session:
        assert await session.get(AccountExchangeIntent, account_id) is None


@pytest.mark.asyncio
async def test_stale_intent_drop_cas_rejects_second_rotation(db_setup, monkeypatch):
    account_id = "stale-cas"
    await _account(account_id)
    encryptor = TokenEncryptor()
    from app.modules.accounts.auth_manager import _refresh_token_material_fingerprint

    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        account = await repo.get_by_id(account_id)
        assert await repo.begin_exchange(
            account_id,
            _refresh_token_material_fingerprint(encryptor, account.refresh_token_encrypted),
            account.refresh_token_encrypted,
        )
        await session.execute(
            text("UPDATE accounts SET refresh_token_encrypted = :token WHERE id = :id"),
            {"token": encryptor.encrypt("tolerant-first"), "id": account_id},
        )
        await session.commit()
    original_drop = AccountsRepository.drop_stale_exchange_intent
    drops = []

    async def rotate_then_drop(self, account_id, stale_hash, expected_token):
        async with SessionLocal() as session:
            await session.execute(
                text("UPDATE accounts SET refresh_token_encrypted = :token WHERE id = :id"),
                {"token": encryptor.encrypt("tolerant-second"), "id": account_id},
            )
            await session.commit()
        result = await original_drop(self, account_id, stale_hash, expected_token)
        drops.append(result)
        return result

    monkeypatch.setattr(AccountsRepository, "drop_stale_exchange_intent", rotate_then_drop)
    calls = 0

    async def transport(self, refresh_token, **kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("a changed token must not reach the provider")

    monkeypatch.setattr(AuthManager, "_refresh_tokens", transport)
    with pytest.raises(RefreshError) as error:
        await _refresh(account_id)
    assert error.value.code == "exchange_intent_conflict"
    assert drops == [False]
    assert calls == 0
    async with SessionLocal() as session:
        assert await session.get(AccountExchangeIntent, account_id) is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [402, 403])
async def test_usage_client_error_cannot_clear_uncertain_snapshot_in_postgres(db_setup, status_code):
    from app.core.clients.usage import UsageFetchError
    from app.modules.usage.repository import UsageRepository
    from app.modules.usage.updater import UsageUpdater

    account_id = f"uncertain-usage-{status_code}"
    await _account(account_id)
    async with SessionLocal() as session:
        await session.execute(
            text("UPDATE accounts SET status = 'exchange_uncertain', deactivation_reason = :reason WHERE id = :id"),
            {"reason": "Refresh exchange outcome uncertain", "id": account_id},
        )
        await session.commit()
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        uncertain_snapshot = await repo.get_by_id(account_id)
        await UsageUpdater(UsageRepository(session), accounts_repo=repo)._deactivate_for_client_error(
            uncertain_snapshot, UsageFetchError(status_code, "rejected")
        )
        stored = await repo.reload_by_id(account_id)
        assert stored.status == AccountStatus.EXCHANGE_UNCERTAIN
        assert stored.deactivation_reason == "Refresh exchange outcome uncertain"


@pytest.mark.asyncio
async def test_status_update_without_token_cannot_clear_uncertain(db_setup):
    account_id = "uncertain-status"
    await _account(account_id)
    async with SessionLocal() as session:
        await session.execute(
            text("UPDATE accounts SET status = 'exchange_uncertain', deactivation_reason = :reason WHERE id = :id"),
            {"reason": "Refresh exchange outcome uncertain", "id": account_id},
        )
        await session.commit()
        repo = AccountsRepository(session)
        assert not await repo.update_status(account_id, AccountStatus.ACTIVE)
        stored = await repo.reload_by_id(account_id)
        assert stored.status == AccountStatus.EXCHANGE_UNCERTAIN
        assert stored.deactivation_reason == "Refresh exchange outcome uncertain"


@pytest.mark.asyncio
async def test_uncertain_account_does_not_drop_matching_token_intent(db_setup):
    account_id = "uncertain-drop"
    await _account(account_id)
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        account = await repo.get_by_id(account_id)
        stale_hash = _refresh_token_material_fingerprint(encryptor, account.refresh_token_encrypted)
        assert await repo.begin_exchange(account_id, stale_hash, account.refresh_token_encrypted)
        await session.execute(
            text("UPDATE accounts SET status = 'exchange_uncertain' WHERE id = :id"), {"id": account_id}
        )
        await session.commit()
        assert not await repo.drop_stale_exchange_intent(account_id, stale_hash, account.refresh_token_encrypted)
    async with SessionLocal() as session:
        assert (await session.get(Account, account_id)).status == AccountStatus.EXCHANGE_UNCERTAIN
        assert await session.get(AccountExchangeIntent, account_id) is not None


@pytest.mark.asyncio
async def test_lost_lock_does_not_drop_new_exchange_intent(db_setup, monkeypatch):
    account_id = "lost-lock"
    await _account(account_id)
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        account = await repo.get_by_id(account_id)
        assert await repo.begin_exchange(
            account_id,
            _refresh_token_material_fingerprint(encryptor, account.refresh_token_encrypted),
            account.refresh_token_encrypted,
        )
        await session.execute(
            text("UPDATE accounts SET refresh_token_encrypted = :token WHERE id = :id"),
            {"token": encryptor.encrypt("current-refresh"), "id": account_id},
        )
        await session.commit()

    @asynccontextmanager
    async def no_lock(_account_id):
        yield

    monkeypatch.setattr(auth_manager_module, "_cross_process_refresh_lock", no_lock)
    both_read = asyncio.Barrier(2)
    first_in_provider = asyncio.Event()
    second_finished = asyncio.Event()
    original_window = AccountsRepository.exchange_intent_window
    original_drop = AccountsRepository.drop_stale_exchange_intent
    workers = {}
    provider_tokens = []

    async def window(self, account_id, timeout):
        result = await original_window(self, account_id, timeout)
        await asyncio.wait_for(both_read.wait(), 10)
        return result

    async def drop(self, account_id, stale_hash, expected_token):
        if workers[id(self)] == "second":
            await asyncio.wait_for(first_in_provider.wait(), 10)
        return await original_drop(self, account_id, stale_hash, expected_token)

    async def transport(self, refresh_token, **kwargs):
        provider_tokens.append(refresh_token)
        kwargs["on_exchange_start"]()
        first_in_provider.set()
        await asyncio.wait_for(second_finished.wait(), 10)
        return TokenRefreshResult("rotated-access", "rotated-refresh", None, None, None, None)

    monkeypatch.setattr(AccountsRepository, "exchange_intent_window", window)
    monkeypatch.setattr(AccountsRepository, "drop_stale_exchange_intent", drop)
    monkeypatch.setattr(AuthManager, "_refresh_tokens", transport)

    async def worker(name):
        async with SessionLocal() as session:
            repo = AccountsRepository(session)
            workers[id(repo)] = name
            account = await repo.get_by_id(account_id)
            snapshot = Account(**{column.key: getattr(account, column.key) for column in Account.__table__.columns})
            try:
                return await AuthManager(repo).refresh_account(snapshot)
            finally:
                if name == "second":
                    second_finished.set()

    first, second = await asyncio.gather(worker("first"), worker("second"), return_exceptions=True)
    assert isinstance(first, Account)
    assert isinstance(second, RefreshError)
    assert second.code == "exchange_intent_conflict"
    assert provider_tokens == ["current-refresh"]
    async with SessionLocal() as session:
        account = await session.get(Account, account_id)
        assert account.status == AccountStatus.ACTIVE
        assert encryptor.decrypt(account.refresh_token_encrypted) == "rotated-refresh"
        assert await session.get(AccountExchangeIntent, account_id) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("session_kind", ["session_local", "background"])
async def test_relogin_between_reload_and_begin_returns_rotated_account(db_setup, monkeypatch, session_kind):
    account_id = f"relogin-before-begin-{session_kind}"
    await _account(account_id)
    encryptor = TokenEncryptor()
    original_begin = AccountsRepository.begin_exchange
    provider_calls = 0

    async def relogin_then_begin(self, account_id, token_hash, expected_token, **kwargs):
        async with SessionLocal() as session:
            assert await AccountsRepository(session).update_tokens(
                account_id,
                encryptor.encrypt("relogin-access"),
                encryptor.encrypt("relogin-refresh"),
                None,
                utcnow(),
            )
        return await original_begin(self, account_id, token_hash, expected_token, **kwargs)

    async def transport(self, refresh_token, **kwargs):
        nonlocal provider_calls
        provider_calls += 1
        raise AssertionError("relogin must not refresh the stale token")

    monkeypatch.setattr(AccountsRepository, "begin_exchange", relogin_then_begin)
    monkeypatch.setattr(AuthManager, "_refresh_tokens", transport)
    refresh = _refresh_in_background_session if session_kind == "background" else _refresh
    refreshed = await refresh(account_id)
    assert refreshed.status == AccountStatus.ACTIVE
    assert encryptor.decrypt(refreshed.refresh_token_encrypted) == "relogin-refresh"
    assert provider_calls == 0
    async with SessionLocal() as session:
        assert await session.get(AccountExchangeIntent, account_id) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("session_kind", ["session_local", "background"])
async def test_relogin_during_exchange_returns_rotated_account(db_setup, monkeypatch, session_kind):
    # The provider answers, but a re-login stored new tokens first, so update_tokens
    # loses its compare-and-swap and the refresh returns the re-login's account.
    account_id = f"relogin-during-exchange-{session_kind}"
    await _account(account_id)
    encryptor = TokenEncryptor()

    async def relogin_then_answer(self, refresh_token, **kwargs):
        kwargs["on_exchange_start"]()
        async with SessionLocal() as session:
            assert await AccountsRepository(session).update_tokens(
                account_id,
                encryptor.encrypt("relogin-access"),
                encryptor.encrypt("relogin-refresh"),
                None,
                utcnow(),
            )
        return TokenRefreshResult("new-access", "new-refresh", None, None, None, None)

    monkeypatch.setattr(AuthManager, "_refresh_tokens", relogin_then_answer)
    refresh = _refresh_in_background_session if session_kind == "background" else _refresh
    refreshed = await refresh(account_id)
    assert refreshed.status == AccountStatus.ACTIVE
    assert encryptor.decrypt(refreshed.refresh_token_encrypted) == "relogin-refresh"
    async with SessionLocal() as session:
        stored = await session.get(Account, account_id)
        assert encryptor.decrypt(stored.refresh_token_encrypted) == "relogin-refresh"
