from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import text

from app.core.auth.refresh import RefreshError, TokenRefreshResult
from app.core.crypto import TokenEncryptor
from app.db.models import Account, AccountExchangeIntent, AccountStatus
from app.db.session import SessionLocal
from app.modules.accounts import auth_manager as auth_manager_module
from app.modules.accounts.auth_manager import AuthManager, _refresh_token_material_fingerprint
from app.modules.accounts.repository import AccountsRepository
from tests.integration.exchange_intent_support import (
    _account,
)

pytestmark = pytest.mark.integration


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
