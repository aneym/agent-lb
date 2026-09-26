"""Durable custody boundaries: a nonce serializes import with abort."""

from __future__ import annotations

import asyncio
import hashlib

import pytest
from sqlalchemy import select

from app.core.config.settings import Settings, get_settings
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, AccountTransfer, AccountTransferDirection, AccountTransferState
from app.db.session import SessionLocal
from app.modules.federation.repository import FederationRepository
from app.modules.federation.schemas import FederationAuthPayload

pytestmark = pytest.mark.integration


def _auth() -> FederationAuthPayload:
    return FederationAuthPayload(
        access_token="new-access",
        refresh_token="new-refresh",
        provider="anthropic",
        email="custody@example.invalid",
        status="active",
        plan_type="pro",
    )


async def _account(account_id: str, owner: str | None) -> None:
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        session.add(
            Account(
                id=account_id,
                provider="anthropic",
                email="custody@example.invalid",
                alias=None,
                plan_type="pro",
                access_token_encrypted=encryptor.encrypt("old-access"),
                refresh_token_encrypted=encryptor.encrypt("old-refresh"),
                last_refresh=utcnow(),
                status=AccountStatus.ACTIVE,
                owner_instance=owner,
            )
        )
        await session.commit()


async def _state(account_id: str) -> tuple[str | None, str, list[AccountTransferState]]:
    async with SessionLocal() as session:
        account = await session.get(Account, account_id)
        rows = (
            (await session.execute(select(AccountTransfer).where(AccountTransfer.account_id == account_id)))
            .scalars()
            .all()
        )
        return (
            account.owner_instance,
            TokenEncryptor().decrypt(account.refresh_token_encrypted),
            [row.state for row in rows],
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("abort_first", [True, False])
async def test_checkout_import_and_reclaim_serialize_on_reservation(db_setup: bool, abort_first: bool) -> None:
    del db_setup
    if SessionLocal.kw["bind"].dialect.name != "postgresql":
        pytest.skip("Requires independent Postgres row locks")
    account_id = "checkout-race"
    nonce = "custody-checkout-race-12345678901234567890"
    await _account(account_id, "studio")
    async with SessionLocal() as session:
        await FederationRepository(session).reserve_checkout(account_id, "studio", nonce)

    entered = asyncio.Event()
    release = asyncio.Event()

    async def importing() -> bool:
        async with SessionLocal() as session:
            repo = FederationRepository(session)
            if not abort_first:
                # Pause after payload receipt but before the commit, holding the nonce row.
                original = session.commit

                async def paused_commit() -> None:
                    entered.set()
                    await release.wait()
                    await original()

                session.commit = paused_commit
            try:
                await repo.import_checkout(
                    account_id, nonce, "studio", _auth(), local_instance_id="forge", encryptor=TokenEncryptor()
                )
                return True
            except ValueError:
                return False

    async def aborting() -> bool:
        async with SessionLocal() as session:
            repo = FederationRepository(session)
            await repo.begin_checkout_abort(nonce)
            await repo.finish_abort(nonce)
            return True

    if abort_first:
        assert await aborting()
        assert not await importing()
        assert await _state(account_id) == ("studio", "old-refresh", [AccountTransferState.ABORTED])
    else:
        task = asyncio.create_task(importing())
        await asyncio.wait_for(entered.wait(), timeout=10)
        abort_task = asyncio.create_task(aborting())
        await asyncio.sleep(0.05)
        release.set()
        assert await asyncio.wait_for(task, timeout=10)
        with pytest.raises(ValueError, match="cannot be aborted"):
            await asyncio.wait_for(abort_task, timeout=10)
        assert await _state(account_id) == ("forge", "new-refresh", [AccountTransferState.SETTLED])


@pytest.mark.asyncio
@pytest.mark.parametrize("abort_first", [True, False])
async def test_checkin_import_and_peer_abort_serialize_on_nonce(db_setup: bool, abort_first: bool) -> None:
    del db_setup
    if SessionLocal.kw["bind"].dialect.name != "postgresql":
        pytest.skip("Requires independent Postgres row locks")
    account_id = "checkin-race"
    nonce = "custody-checkin-race-12345678901234567890"
    await _account(account_id, "forge")
    async with SessionLocal() as session:
        repo = FederationRepository(session)
        await repo.create_transfer(
            account_id=account_id,
            direction=AccountTransferDirection.CHECKOUT,
            counterparty_instance_id="forge",
            nonce="prior-checkout-checkin-race",
        )
        await repo.mark_transfer_settled("prior-checkout-checkin-race")
    entered = asyncio.Event()
    release = asyncio.Event()

    async def importing() -> bool:
        async with SessionLocal() as session:
            repo = FederationRepository(session)
            if not abort_first:
                original = session.commit

                async def paused_commit() -> None:
                    entered.set()
                    await release.wait()
                    await original()

                session.commit = paused_commit
            try:
                await repo.accept_checkin(account_id, nonce, "forge", _auth(), encryptor=TokenEncryptor())
                return True
            except ValueError:
                return False

    async def aborting() -> str:
        async with SessionLocal() as session:
            return (
                await FederationRepository(session).abort_peer_transfer(
                    nonce, account_id, AccountTransferDirection.CHECKIN, "forge", local_instance_id="studio"
                )
            ).value

    if abort_first:
        assert await aborting() == "aborted"
        assert not await importing()
        assert await _state(account_id) == (
            "forge",
            "old-refresh",
            [AccountTransferState.SETTLED, AccountTransferState.ABORTED],
        )
    else:
        task = asyncio.create_task(importing())
        await asyncio.wait_for(entered.wait(), timeout=10)
        abort_task = asyncio.create_task(aborting())
        await asyncio.sleep(0.05)
        release.set()
        assert await asyncio.wait_for(task, timeout=10)
        assert await asyncio.wait_for(abort_task, timeout=10) == "settled"
        assert await _state(account_id) == (
            None,
            "new-refresh",
            [AccountTransferState.SETTLED, AccountTransferState.SETTLED],
        )


@pytest.mark.asyncio
async def test_studio_shaped_caller_cannot_reclaim_pending_checkout(
    async_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _account("studio-pending", "forge")
    async with SessionLocal() as session:
        await FederationRepository(session).create_transfer(
            account_id="studio-pending",
            direction=AccountTransferDirection.CHECKOUT,
            counterparty_instance_id="forge",
            nonce="pending-studio-nonce-12345678901234567890",
        )
    mirror = "mirror-only"
    monkeypatch.setenv("AGENT_LB_FEDERATION_MIRROR_TOKEN", mirror)
    monkeypatch.setenv("AGENT_LB_FEDERATION_TRANSFER_INBOUND_SHA256", hashlib.sha256(b"forge-only").hexdigest())
    monkeypatch.setenv("AGENT_LB_FEDERATION_TAKER_INSTANCE_IDS", "forge")
    monkeypatch.delenv("AGENT_LB_FEDERATION_TRANSFER_OUTBOUND_TOKEN", raising=False)
    get_settings.cache_clear()
    response = await async_client.post("/api/federation/reclaim/studio-pending", json={})
    assert response.status_code == 503
    assert await _state("studio-pending") == ("forge", "old-refresh", [AccountTransferState.PENDING])
    peer = await async_client.post(
        "/api/federation/transfers/pending-studio-nonce-12345678901234567890/abort",
        json={"account_id": "studio-pending", "direction": "checkout", "caller_instance_id": "forge"},
        headers={"Authorization": "Bearer mirror-only"},
    )
    assert peer.status_code == 403
    assert await _state("studio-pending") == ("forge", "old-refresh", [AccountTransferState.PENDING])


def test_transfer_settings_fail_closed_on_legacy_and_equal_credentials() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="obsolete"):
        Settings(federation_transfer_token="obsolete")
    with pytest.raises(ValidationError, match="64 lowercase hex"):
        Settings(federation_transfer_inbound_sha256="INVALID")
    with pytest.raises(ValidationError, match="must differ"):
        Settings(
            federation_transfer_outbound_token="other",
            federation_transfer_inbound_sha256=hashlib.sha256(b"other").hexdigest(),
        )
    with pytest.raises(ValidationError, match="must differ"):
        Settings(federation_token="mirror", federation_transfer_inbound_sha256=hashlib.sha256(b"mirror").hexdigest())
