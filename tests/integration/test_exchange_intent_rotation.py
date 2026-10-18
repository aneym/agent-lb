from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import text

from app.core.auth.refresh import RefreshError, TokenRefreshResult
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import AccountExchangeIntent, AccountStatus
from app.db.session import SessionLocal
from app.modules.accounts.auth_manager import AuthManager
from app.modules.accounts.repository import AccountsRepository
from tests.integration.exchange_intent_support import (
    _account,
    _refresh,
)

pytestmark = pytest.mark.integration


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
    assert calls == 2
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
