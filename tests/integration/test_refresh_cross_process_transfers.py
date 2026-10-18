from __future__ import annotations
import asyncio
import os
import secrets
from datetime import timedelta
import pytest
from app.core.auth.refresh import RefreshError, TokenRefreshResult
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal
from app.modules.accounts.auth_manager import AuthManager
from app.modules.accounts.repository import AccountsRepository

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        "postgresql" not in os.environ.get("AGENT_LB_TEST_DATABASE_URL", ""),
        reason="cross-process refresh lock is a Postgres advisory lock",
    ),
]


@pytest.mark.asyncio
async def test_checkout_waits_for_refresh_and_exports_rotated_token(db_setup, monkeypatch):
    from app.modules.federation.repository import FederationRepository
    from app.modules.federation.service import FederationService

    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        await AccountsRepository(session).upsert(
            Account(
                id="acc-checkout-refresh",
                email="checkout-refresh@example.com",
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

    async def stalled(self, token: str, **_kwargs):
        entered.set()
        await release.wait()
        return TokenRefreshResult("access-after", "refresh-after", "id-after", None, None, None)

    monkeypatch.setattr(AuthManager, "_refresh_tokens", stalled)

    async def worker():
        async with SessionLocal() as session:
            repo = AccountsRepository(session)
            return await AuthManager(repo).refresh_account(await repo.get_by_id("acc-checkout-refresh"))

    first = asyncio.create_task(worker())
    await asyncio.wait_for(entered.wait(), 5)

    async def checkout():

        nonce = secrets.token_hex(24)
        async with SessionLocal() as session:
            return await FederationService(FederationRepository(session)).checkout(
                "acc-checkout-refresh", "taker", nonce
            )

    taker = asyncio.create_task(checkout())
    await asyncio.sleep(0.1)
    assert not taker.done()
    release.set()
    await asyncio.wait_for(first, 10)
    result = await asyncio.wait_for(taker, 10)
    assert result.auth.refresh_token == "refresh-after"
    assert result.auth.access_token == "access-after"


@pytest.mark.asyncio
async def test_lost_lock_checkout_before_intent_cannot_spend_exported_token(db_setup):

    from app.db.models import AccountExchangeIntent
    from app.modules.federation.repository import FederationRepository
    from app.modules.federation.service import FederationService

    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        await AccountsRepository(session).upsert(
            Account(
                id="lost-fence",
                email="lost-fence@example.com",
                provider="anthropic",
                plan_type="claude",
                access_token_encrypted=encryptor.encrypt("access-live"),
                refresh_token_encrypted=encryptor.encrypt("refresh-live"),
                last_refresh=utcnow(),
                status=AccountStatus.ACTIVE,
            )
        )
    nonce = secrets.token_hex(24)
    async with SessionLocal() as session:
        result = await FederationService(FederationRepository(session)).checkout("lost-fence", "taker", nonce)
        assert result.auth.refresh_token == "refresh-live"
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        assert not await repo.begin_exchange("lost-fence", "fingerprint", encryptor.encrypt("refresh-live"))
        assert await session.get(AccountExchangeIntent, "lost-fence") is None


@pytest.mark.asyncio
async def test_checkin_waits_for_refresh_and_returns_rotated_token(db_setup, monkeypatch):

    from app.core.config.settings import Settings
    from app.db.models import AccountTransferDirection
    from app.modules.federation.peer_client import CheckinPeerResult
    from app.modules.federation.repository import FederationRepository
    from app.modules.federation.service import FederationService

    monkeypatch.setenv("AGENT_LB_LOCAL_INSTANCE_ID", "taker")
    from app.core.config.settings import get_settings

    get_settings.cache_clear()
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        await AccountsRepository(session).upsert(
            Account(
                id="return-refresh",
                email="return-refresh@example.com",
                provider="anthropic",
                plan_type="claude",
                access_token_encrypted=encryptor.encrypt("access-before"),
                refresh_token_encrypted=encryptor.encrypt("refresh-before"),
                last_refresh=utcnow(),
                status=AccountStatus.ACTIVE,
                owner_instance="taker",
            )
        )
        federation_repo = FederationRepository(session)
        await federation_repo.create_transfer(
            account_id="return-refresh",
            direction=AccountTransferDirection.CHECKOUT,
            counterparty_instance_id="studio",
            nonce="prior-checkout",
        )
        await federation_repo.mark_transfer_settled("prior-checkout")
    entered, release = asyncio.Event(), asyncio.Event()
    returned: list[str] = []

    async def stalled(self, token, **kwargs):
        kwargs["on_exchange_start"]()
        entered.set()
        await release.wait()
        return TokenRefreshResult("access-after", "refresh-after", None, None, None, None)

    class Peer:
        async def checkin(self, *, auth, **kwargs):
            returned.append(auth.refresh_token)
            return CheckinPeerResult(settled=True)

    monkeypatch.setattr(AuthManager, "_refresh_tokens", stalled)

    async def refresh():
        async with SessionLocal() as session:
            repo = AccountsRepository(session)
            return await AuthManager(repo).refresh_account(await repo.get_by_id("return-refresh"))

    first = asyncio.create_task(refresh())
    await asyncio.wait_for(entered.wait(), 5)

    async def checkin():
        async with SessionLocal() as session:
            return await FederationService(
                FederationRepository(session),
                settings=Settings(
                    local_instance_id="taker",
                    federation_peer_url="https://peer.invalid",
                    federation_transfer_outbound_token="dummy",
                ),
                peer_client=Peer(),
            ).execute_checkin("return-refresh")

    second = asyncio.create_task(checkin())
    await asyncio.sleep(0.1)
    assert not second.done()
    release.set()
    await asyncio.wait_for(first, 10)
    await asyncio.wait_for(second, 10)
    assert returned == ["refresh-after"]
    async with SessionLocal() as session:
        stored = await session.get(Account, "return-refresh")
        assert stored.owner_instance == "studio"
        assert encryptor.decrypt(stored.refresh_token_encrypted) == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("transfer_state", ["pending", "aborting"])
async def test_exchange_intent_during_transfer_never_refreshes_by_non_owner(db_setup, monkeypatch, transfer_state):
    from app.core.config.settings import get_settings
    from app.db.models import AccountExchangeIntent, AccountTransferDirection, AccountTransferState
    from app.modules.accounts.auth_manager import AccountNotOwnedError, _refresh_token_material_fingerprint
    from app.modules.federation.repository import FederationRepository

    monkeypatch.setenv("AGENT_LB_LOCAL_INSTANCE_ID", "studio")
    get_settings.cache_clear()
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        await AccountsRepository(session).upsert(
            Account(
                id="transfer-intent",
                email="transfer-intent@example.invalid",
                provider="anthropic",
                plan_type="claude",
                access_token_encrypted=encryptor.encrypt("access-live"),
                refresh_token_encrypted=encryptor.encrypt("refresh-live"),
                last_refresh=utcnow(),
                access_expires_at=utcnow() + timedelta(hours=1),
                status=AccountStatus.ACTIVE,
                owner_instance="forge",
            )
        )
        transfer = await FederationRepository(session).create_transfer(
            account_id="transfer-intent",
            direction=AccountTransferDirection.CHECKIN,
            counterparty_instance_id="forge",
            nonce=secrets.token_hex(24),
        )
        if transfer_state == "aborting":
            transfer.state = AccountTransferState.ABORTING
            await session.commit()
        account = await session.get(Account, "transfer-intent")
        session.add(
            AccountExchangeIntent(
                account_id=account.id,
                refresh_token_sha256=_refresh_token_material_fingerprint(encryptor, account.refresh_token_encrypted),
                started_at=utcnow(),
            )
        )
        await session.commit()

    async def forbidden_refresh(self, token, **kwargs):
        pytest.fail("non-owner sent a refresh token during transfer")

    monkeypatch.setattr(AuthManager, "_refresh_tokens", forbidden_refresh)
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        account = await repo.get_by_id("transfer-intent")
        served = await AuthManager(repo).ensure_fresh(account)
        assert encryptor.decrypt(served.access_token_encrypted) == "access-live"
        with pytest.raises(AccountNotOwnedError):
            await AuthManager(repo).refresh_account(account)
        with pytest.raises(AccountNotOwnedError):
            await AuthManager(repo).ensure_fresh(account, force=True)
    async with SessionLocal() as session:
        assert (await session.get(Account, "transfer-intent")).owner_instance == "forge"
        assert await session.get(AccountExchangeIntent, "transfer-intent") is not None
        assert not await FederationRepository(session).upsert_mirror_account(
            account_id="transfer-intent",
            provider="anthropic",
            email="transfer-intent@example.invalid",
            alias=None,
            status="active",
            plan_type="claude",
            chatgpt_account_id=None,
            access_token="stale-mirror",
            owner_instance_id="forge",
            local_instance_id="studio",
            encryptor=encryptor,
        )
        account = await session.get(Account, "transfer-intent")
        assert encryptor.decrypt(account.refresh_token_encrypted) == "refresh-live"


@pytest.mark.asyncio
async def test_mirrored_row_cannot_redeem_refresh_token(db_setup, monkeypatch):
    from app.core.config.settings import get_settings
    from app.modules.accounts.auth_manager import AccountNotOwnedError
    from app.modules.federation.repository import FederationRepository

    monkeypatch.setenv("AGENT_LB_LOCAL_INSTANCE_ID", "studio")
    get_settings.cache_clear()
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        assert await FederationRepository(session).upsert_mirror_account(
            account_id="mirror-no-refresh",
            provider="anthropic",
            email="mirror@example.invalid",
            alias=None,
            status="active",
            plan_type="claude",
            chatgpt_account_id=None,
            access_token="mirror-access",
            owner_instance_id="forge",
            local_instance_id="studio",
            encryptor=encryptor,
            expires_at_ms=int((utcnow() + timedelta(hours=1)).timestamp() * 1000),
        )

    async def forbidden_refresh(self, token, **kwargs):
        pytest.fail("mirror sent a refresh token to the provider")

    monkeypatch.setattr(AuthManager, "_refresh_tokens", forbidden_refresh)
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        mirror = await repo.get_by_id("mirror-no-refresh")
        assert encryptor.decrypt(mirror.refresh_token_encrypted) == ""
        served = await AuthManager(repo).ensure_fresh(mirror)
        assert encryptor.decrypt(served.access_token_encrypted) == "mirror-access"
        with pytest.raises(AccountNotOwnedError):
            await AuthManager(repo).refresh_account(mirror)
        with pytest.raises(AccountNotOwnedError):
            await AuthManager(repo).ensure_fresh(mirror, force=True)
