from __future__ import annotations

import asyncio

import pytest

from app.core.config.settings import Settings
from app.core.crypto import TokenEncryptor
from app.db.models import AccountTransferDirection, AccountTransferState
from app.db.session import SessionLocal
from app.modules.federation.repository import FederationRepository
from tests.integration.federation_custody_races_support import (
    _account,
    _auth,
    _state,
)

pytestmark = pytest.mark.integration


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
        with pytest.raises(ValueError, match="cannot be aborted|transfer no longer open"):
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
async def test_reclaim_racing_committed_import_never_aborts(db_setup: bool) -> None:
    del db_setup
    if SessionLocal.kw["bind"].dialect.name != "postgresql":
        pytest.skip("Requires independent Postgres row locks")
    from app.modules.federation.service import FederationService

    account_id = "reclaim-import-race"
    nonce = "reclaim-import-nonce-12345678901234567890"
    await _account(account_id, "studio")
    async with SessionLocal() as session:
        await FederationRepository(session).reserve_checkout(account_id, "studio", nonce)

    entered, release = asyncio.Event(), asyncio.Event()

    async def import_account() -> None:
        async with SessionLocal() as session:
            original_commit = session.commit

            async def paused_commit() -> None:
                entered.set()
                await release.wait()
                await original_commit()

            session.commit = paused_commit
            await FederationRepository(session).import_checkout(
                account_id, nonce, "studio", _auth(), local_instance_id="forge", encryptor=TokenEncryptor()
            )

    class Giver:
        abort_calls = 0

        async def abort(self, **kwargs):
            self.abort_calls += 1
            return "aborted"

        async def checkout_confirm(self, **kwargs):
            return None

    giver = Giver()

    async def reclaim() -> bool:
        async with SessionLocal() as session:
            return await FederationService(
                FederationRepository(session),
                settings=Settings(
                    local_instance_id="forge",
                    federation_peer_url="http://studio.invalid",
                    federation_transfer_outbound_token="outbound-forge",
                ),
                peer_client=giver,
            ).reclaim(account_id)

    import_task = asyncio.create_task(import_account())
    await asyncio.wait_for(entered.wait(), 10)
    reclaim_task = asyncio.create_task(reclaim())
    await asyncio.sleep(0.1)
    release.set()
    await asyncio.wait_for(import_task, 10)
    assert await asyncio.wait_for(reclaim_task, 10)
    assert giver.abort_calls == 0
    assert await _state(account_id) == ("forge", "new-refresh", [AccountTransferState.SETTLED])


@pytest.mark.asyncio
async def test_two_concurrent_taker_checkouts_have_one_reservation(db_setup: bool) -> None:
    del db_setup
    if SessionLocal.kw["bind"].dialect.name != "postgresql":
        pytest.skip("Requires independent Postgres row locks")
    from app.modules.federation.exceptions import FederationConflictError
    from app.modules.federation.peer_client import CheckoutPeerResult
    from app.modules.federation.service import FederationService

    account_id = "concurrent-taker-checkout"
    await _account(account_id, "studio")

    class Giver:
        def __init__(self) -> None:
            self.nonce: str | None = None

        async def checkout(self, *, nonce: str, **kwargs) -> CheckoutPeerResult:
            await asyncio.sleep(0.05)
            if self.nonce is not None:
                raise AssertionError("second checkout reached the peer")
            self.nonce = nonce
            return CheckoutPeerResult(nonce=nonce, owner_instance_id="studio", auth=_auth())

        async def checkout_confirm(self, **kwargs) -> None:
            return None

    giver = Giver()

    async def checkout() -> bool:
        async with SessionLocal() as session:
            try:
                await FederationService(
                    FederationRepository(session),
                    settings=Settings(
                        local_instance_id="forge",
                        federation_peer_url="http://studio.invalid",
                        federation_transfer_outbound_token="outbound-forge",
                    ),
                    peer_client=giver,
                ).execute_checkout(account_id)
                return True
            except FederationConflictError:
                return False

    assert sorted(await asyncio.gather(checkout(), checkout())) == [False, True]
    assert giver.nonce is not None
    assert await _state(account_id) == ("forge", "new-refresh", [AccountTransferState.SETTLED])


@pytest.mark.asyncio
@pytest.mark.parametrize("abort_first", [True, False])
async def test_checkout_confirm_and_giver_abort_serialize_on_account(db_setup: bool, abort_first: bool) -> None:
    del db_setup
    if SessionLocal.kw["bind"].dialect.name != "postgresql":
        pytest.skip("Requires independent Postgres row locks")
    account_id = "confirm-abort-race"
    nonce = "confirm-abort-nonce-12345678901234567890"
    await _account(account_id, None)
    async with SessionLocal() as session:
        await FederationRepository(session).release_for_checkout(
            account_id, "forge", local_instance_id="studio", nonce=nonce
        )
    entered, release = asyncio.Event(), asyncio.Event()

    async def abort() -> AccountTransferState:
        async with SessionLocal() as session:
            if abort_first:
                original_commit = session.commit

                async def paused_commit() -> None:
                    entered.set()
                    await release.wait()
                    await original_commit()

                session.commit = paused_commit
            return await FederationRepository(session).abort_peer_transfer(
                nonce, account_id, AccountTransferDirection.CHECKOUT, "forge", local_instance_id="studio"
            )

    async def confirm() -> bool:
        async with SessionLocal() as session:
            if not abort_first:
                original_commit = session.commit

                async def paused_commit() -> None:
                    entered.set()
                    await release.wait()
                    await original_commit()

                session.commit = paused_commit
            return (
                await FederationRepository(session).settle_checkout_and_blank_refresh(nonce, encryptor=TokenEncryptor())
                is not None
            )

    first = asyncio.create_task(abort() if abort_first else confirm())
    await asyncio.wait_for(entered.wait(), 10)
    second = asyncio.create_task(confirm() if abort_first else abort())
    await asyncio.sleep(0.05)
    release.set()
    first_result = await asyncio.wait_for(first, 10)
    second_result = await asyncio.wait_for(second, 10)
    if abort_first:
        assert (first_result, second_result) == (AccountTransferState.ABORTED, False)
        assert await _state(account_id) == ("studio", "old-refresh", [AccountTransferState.ABORTED])
    else:
        assert (first_result, second_result) == (True, AccountTransferState.SETTLED)
        assert await _state(account_id) == ("forge", "", [AccountTransferState.SETTLED])
