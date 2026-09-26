from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import text

from app.core.auth.exchange_phase import ExchangePhase
from app.core.auth.refresh import RefreshError, TokenRefreshResult
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountExchangeIntent, AccountStatus
from app.db.session import SessionLocal
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
