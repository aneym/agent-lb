from __future__ import annotations

import base64
import json
from datetime import timedelta

import pytest
from sqlalchemy import text

from app.core.auth.refresh import TokenRefreshResult
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountExchangeIntent, AccountStatus
from app.db.session import SessionLocal, get_background_session
from app.modules.accounts.auth_manager import AuthManager
from app.modules.accounts.repository import AccountsRepository
from tests.integration.exchange_intent_support import (
    _account,
    _refresh,
    _refresh_in_background_session,
)

pytestmark = pytest.mark.integration


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


@pytest.mark.asyncio
@pytest.mark.parametrize("session_kind", ["session_local", "background"])
async def test_rotation_during_chatgpt_account_id_backfill_returns_rotated_account(db_setup, monkeypatch, session_kind):

    account_id = f"backfill-race-{session_kind}"
    encryptor = TokenEncryptor()
    claims = base64.urlsafe_b64encode(json.dumps({"chatgpt_account_id": "acct-backfill"}).encode()).decode().rstrip("=")
    async with SessionLocal() as session:
        await AccountsRepository(session).upsert(
            Account(
                id=account_id,
                provider="openai",
                email=f"{account_id}@example.invalid",
                plan_type="plus",
                access_token_encrypted=encryptor.encrypt("access"),
                refresh_token_encrypted=encryptor.encrypt("refresh"),
                id_token_encrypted=encryptor.encrypt(f"header.{claims}.signature"),
                last_refresh=utcnow(),
                access_expires_at=utcnow() + timedelta(hours=1),
                status=AccountStatus.ACTIVE,
            )
        )
        await session.execute(text("UPDATE accounts SET chatgpt_account_id = NULL WHERE id = :id"), {"id": account_id})
        await session.commit()
    original_update_tokens = AccountsRepository.update_tokens
    rotated = False

    async def rotate_then_update(self, *args, **kwargs):
        nonlocal rotated
        if not rotated:
            rotated = True
            async with SessionLocal() as other:
                assert await original_update_tokens(
                    AccountsRepository(other),
                    account_id,
                    encryptor.encrypt("rotated-access"),
                    encryptor.encrypt("rotated-refresh"),
                    None,
                    utcnow(),
                )
        return await original_update_tokens(self, *args, **kwargs)

    monkeypatch.setattr(AccountsRepository, "update_tokens", rotate_then_update)
    session_scope = get_background_session() if session_kind == "background" else SessionLocal()
    async with session_scope as session:
        repo = AccountsRepository(session)
        account = await repo.get_by_id(account_id)
        assert account is not None and account.chatgpt_account_id is None
        fresh = await AuthManager(repo).ensure_fresh(account)
    assert rotated
    assert encryptor.decrypt(fresh.refresh_token_encrypted) == "rotated-refresh"
    assert fresh.status == AccountStatus.ACTIVE


@pytest.mark.asyncio
@pytest.mark.parametrize("session_kind", ["session_local", "background"])
async def test_account_deleted_before_ensure_fresh_returns_detached_copy(db_setup, session_kind):

    account_id = f"deleted-before-fresh-{session_kind}"
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        await AccountsRepository(session).upsert(
            Account(
                id=account_id,
                provider="openai",
                email=f"{account_id}@example.invalid",
                plan_type="plus",
                chatgpt_account_id="acct-present",
                access_token_encrypted=encryptor.encrypt("access"),
                refresh_token_encrypted=encryptor.encrypt("refresh"),
                id_token_encrypted=encryptor.encrypt("id"),
                last_refresh=utcnow(),
                access_expires_at=utcnow() + timedelta(hours=1),
                status=AccountStatus.ACTIVE,
            )
        )
    session_scope = get_background_session() if session_kind == "background" else SessionLocal()
    async with session_scope as session:
        repo = AccountsRepository(session)
        account = await repo.get_by_id(account_id)
        assert account is not None
        async with SessionLocal() as other:
            assert await AccountsRepository(other).delete(account_id)
        fresh = await AuthManager(repo).ensure_fresh(account)
    columns = {column.key: getattr(fresh, column.key) for column in Account.__table__.columns}
    assert columns["id"] == account_id
