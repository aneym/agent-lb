from __future__ import annotations
import asyncio
import os
from datetime import timedelta
import pytest
from app.core.auth.refresh import RefreshError, TokenRefreshResult
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal
from app.modules.accounts.auth_manager import AuthManager
from app.modules.accounts.repository import AccountsRepository
from tests.integration.refresh_cross_process_support import (
    _RotatingTokenEndpoint,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        "postgresql" not in os.environ.get("AGENT_LB_TEST_DATABASE_URL", ""),
        reason="cross-process refresh lock is a Postgres advisory lock",
    ),
]


@pytest.mark.asyncio
async def test_two_processes_refreshing_one_account_present_the_token_once(db_setup, monkeypatch):
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        await AccountsRepository(session).upsert(
            Account(
                id="acc-refresh-race",
                email="race@example.com",
                plan_type="plus",
                access_token_encrypted=encryptor.encrypt("access-0"),
                refresh_token_encrypted=encryptor.encrypt("refresh-0"),
                id_token_encrypted=encryptor.encrypt("id-0"),
                last_refresh=utcnow(),
                status=AccountStatus.ACTIVE,
                deactivation_reason=None,
            )
        )

    endpoint = _RotatingTokenEndpoint()

    async def _refresh_tokens(self: AuthManager, refresh_token: str, **_: object) -> TokenRefreshResult:
        return await endpoint.refresh(refresh_token)

    monkeypatch.setattr(AuthManager, "_refresh_tokens", _refresh_tokens)

    async def one_process() -> Account:
        async with SessionLocal() as session:
            repo = AccountsRepository(session)
            account = await repo.get_by_id("acc-refresh-race")
            assert account is not None
            return await AuthManager(repo).refresh_account(account)

    first, second = await asyncio.gather(one_process(), one_process())

    assert endpoint.presented == ["refresh-0"]
    assert encryptor.decrypt(first.refresh_token_encrypted) == "refresh-1"
    assert encryptor.decrypt(second.refresh_token_encrypted) == "refresh-1"
    async with SessionLocal() as session:
        stored = await AccountsRepository(session).get_by_id("acc-refresh-race")
    assert stored is not None
    assert stored.status == AccountStatus.ACTIVE
    assert encryptor.decrypt(stored.refresh_token_encrypted) == "refresh-1"


@pytest.mark.asyncio
async def test_lost_refresh_lock_does_not_replay_spent_token(db_setup, monkeypatch):

    from sqlalchemy import text

    from app.db import session as db_session
    from app.modules.accounts import auth_manager as auth_module

    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        await AccountsRepository(session).upsert(
            Account(
                id="acc-lost-lock",
                email="lost-lock@example.com",
                plan_type="plus",
                access_token_encrypted=encryptor.encrypt("access-before"),
                refresh_token_encrypted=encryptor.encrypt("refresh-before"),
                id_token_encrypted=encryptor.encrypt("id-before"),
                last_refresh=utcnow(),
                status=AccountStatus.ACTIVE,
            )
        )

    entered = asyncio.Event()
    release = asyncio.Event()
    presented: list[str] = []

    async def stalled(self, token: str, **_kwargs):
        _kwargs["on_exchange_start"]()
        presented.append(token)
        entered.set()
        await release.wait()
        return TokenRefreshResult("access-after", "refresh-after", "id-after", None, None, None)

    monkeypatch.setattr(AuthManager, "_refresh_tokens", stalled)

    async def worker():
        async with SessionLocal() as session:
            repo = AccountsRepository(session)
            account = await repo.get_by_id("acc-lost-lock")
            return await AuthManager(repo).refresh_account(account)

    first = asyncio.create_task(worker())
    await asyncio.wait_for(entered.wait(), 5)

    key = auth_module._refresh_lock_key("acc-lost-lock")
    unsigned_key = key % (1 << 64)
    async with db_session.engine.connect() as conn:
        pid = (
            await conn.execute(
                text("""
            SELECT pid FROM pg_locks
            WHERE locktype = 'advisory' AND granted
              AND classid = :upper AND objid = :lower
            LIMIT 1
        """),
                {"upper": unsigned_key >> 32, "lower": unsigned_key & 0xFFFFFFFF},
            )
        ).scalar_one_or_none()
        assert pid is not None
        await conn.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": pid})
        await conn.commit()
    with pytest.raises(RefreshError) as second:
        await asyncio.wait_for(worker(), 10)
    assert second.value.code == "exchange_uncertain"
    release.set()

    with pytest.raises(Exception):
        await asyncio.wait_for(first, 10)
    assert len(presented) == 1
    async with SessionLocal() as session:
        stored = await session.get(Account, "acc-lost-lock")
        assert stored.status == AccountStatus.ACTIVE
        assert encryptor.decrypt(stored.refresh_token_encrypted) == "refresh-after"


@pytest.mark.asyncio
async def test_preflight_rejection_releases_fence_and_reauthorization_clears_stale_intent(db_setup, monkeypatch):

    from app.db.models import AccountExchangeIntent

    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        await AccountsRepository(session).upsert(
            Account(
                id="preflight-account",
                email="preflight@example.com",
                provider="anthropic",
                plan_type="claude",
                access_token_encrypted=encryptor.encrypt("access-before"),
                refresh_token_encrypted=encryptor.encrypt("refresh-before"),
                last_refresh=utcnow(),
                status=AccountStatus.ACTIVE,
            )
        )

    async def reject():
        raise RefreshError("admission_rejected", "Busy", False)

    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        with pytest.raises(RefreshError) as error:
            await AuthManager(repo, acquire_refresh_admission=reject).refresh_account(
                await repo.get_by_id("preflight-account")
            )
        assert error.value.code == "admission_rejected"
    async with SessionLocal() as session:
        assert await session.get(AccountExchangeIntent, "preflight-account") is None
        assert (await session.get(Account, "preflight-account")).status == AccountStatus.ACTIVE

    async def failed_provider(self, token, **kwargs):
        kwargs["on_exchange_start"]()
        raise RefreshError("503", "Unavailable", False)

    monkeypatch.setattr(AuthManager, "_refresh_tokens", failed_provider)
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        with pytest.raises(RefreshError) as error:
            await AuthManager(repo).refresh_account(await repo.get_by_id("preflight-account"))
        assert error.value.code == "exchange_uncertain"
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        await repo.upsert(
            Account(
                id="preflight-account",
                email="preflight@example.com",
                provider="anthropic",
                plan_type="claude",
                access_token_encrypted=encryptor.encrypt("access-login"),
                refresh_token_encrypted=encryptor.encrypt("refresh-login"),
                last_refresh=utcnow(),
                status=AccountStatus.ACTIVE,
            )
        )
        assert await session.get(AccountExchangeIntent, "preflight-account") is None

    async def succeeded(self, token, **kwargs):
        kwargs["on_exchange_start"]()
        assert token == "refresh-login"
        return TokenRefreshResult("access-new", "refresh-new", None, None, None, None)

    monkeypatch.setattr(AuthManager, "_refresh_tokens", succeeded)
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        updated = await AuthManager(repo).refresh_account(await repo.get_by_id("preflight-account"))
        assert encryptor.decrypt(updated.refresh_token_encrypted) == "refresh-new"


@pytest.mark.asyncio
async def test_operator_reset_waits_for_window_then_replays(async_client, db_setup):

    from app.db.models import AccountExchangeIntent

    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        await repo.upsert(
            Account(
                id="reset-account",
                email="reset@example.com",
                provider="anthropic",
                plan_type="claude",
                access_token_encrypted=encryptor.encrypt("access"),
                refresh_token_encrypted=encryptor.encrypt("refresh"),
                last_refresh=utcnow(),
                status=AccountStatus.ACTIVE,
            )
        )
        account = await repo.get_by_id("reset-account")
        from app.modules.accounts.auth_manager import _refresh_token_material_fingerprint

        fingerprint = _refresh_token_material_fingerprint(encryptor, account.refresh_token_encrypted)
        assert await repo.begin_exchange("reset-account", fingerprint, account.refresh_token_encrypted)
        await repo.mark_exchange_uncertain("reset-account", fingerprint, account.refresh_token_encrypted)
    assert (await async_client.post("/api/accounts/reset-account/reactivate")).status_code == 409
    response = await async_client.post("/api/accounts/reset-account/exchange-reset")
    assert response.status_code == 409
    assert "available at" in response.text
    async with SessionLocal() as session:
        assert await session.get(AccountExchangeIntent, "reset-account") is not None
        assert (await session.get(Account, "reset-account")).status == AccountStatus.EXCHANGE_UNCERTAIN
        intent = await session.get(AccountExchangeIntent, "reset-account")
        intent.started_at = utcnow() - timedelta(seconds=1000)
        await session.commit()
    response = await async_client.post("/api/accounts/reset-account/exchange-reset")
    assert response.status_code == 200
    assert response.json()["status"] == "active"
    assert "re-login" in response.json()["message"]
    async with SessionLocal() as session:
        assert (await session.get(AccountExchangeIntent, "reset-account")).reason == "operator_reset"
        assert (await session.get(Account, "reset-account")).status == AccountStatus.ACTIVE
