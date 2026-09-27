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
    monkeypatch.setenv("AGENT_LB_FEDERATION_PEER_URL", "http://forge.invalid:2455")
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
    with pytest.raises(ValidationError, match="must differ"):
        Settings(federation_mirror_token="mirror", federation_transfer_outbound_token="mirror")


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
async def test_reservation_refuses_changed_owner(db_setup: bool) -> None:
    del db_setup
    account_id = "reservation-owner-changed"
    await _account(account_id, "studio")
    async with SessionLocal() as stale, SessionLocal() as writer:
        assert (await stale.get(Account, account_id)).owner_instance == "studio"
        changed = await writer.get(Account, account_id)
        changed.owner_instance = "other"
        await writer.commit()
        with pytest.raises(ValueError, match="owner changed"):
            await FederationRepository(stale).reserve_checkout(
                account_id, "studio", "owner-changed-nonce-12345678901234567890"
            )
    assert await _state(account_id) == ("other", "old-refresh", [])


@pytest.mark.asyncio
async def test_reclaim_settled_checkout_confirms_without_abort(db_setup: bool) -> None:
    del db_setup
    from app.modules.federation.service import FederationService

    account_id = "settled-checkout-reclaim"
    nonce = "settled-reclaim-nonce-12345678901234567890"
    await _account(account_id, "studio")
    async with SessionLocal() as session:
        repo = FederationRepository(session)
        await repo.reserve_checkout(account_id, "studio", nonce)
        await repo.import_checkout(
            account_id, nonce, "studio", _auth(), local_instance_id="forge", encryptor=TokenEncryptor()
        )

    class Giver:
        confirms = 0

        async def checkout_confirm(self, **kwargs) -> None:
            self.confirms += 1

        async def abort(self, **kwargs) -> None:
            raise AssertionError("settled checkout must never abort")

    giver = Giver()
    async with SessionLocal() as session:
        assert await FederationService(
            FederationRepository(session),
            settings=Settings(
                local_instance_id="forge",
                federation_peer_url="http://studio.invalid",
                federation_transfer_outbound_token="outbound-forge",
            ),
            peer_client=giver,
        ).reclaim(account_id)
    assert giver.confirms == 1
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


@pytest.mark.asyncio
async def test_checkin_refusals_remain_conflicts_after_rollback(async_client, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable = hashlib.sha256(b"checkin-route-credential").hexdigest()
    monkeypatch.setenv("AGENT_LB_FEDERATION_TRANSFER_INBOUND_SHA256", _enable)
    monkeypatch.setenv("AGENT_LB_FEDERATION_TAKER_INSTANCE_IDS", "forge")
    monkeypatch.setenv("AGENT_LB_LOCAL_INSTANCE_ID", "studio")
    get_settings.cache_clear()
    for account_id, owner in (
        ("wrong-owner", "other"),
        ("no-checkout", "forge"),
        ("local-owner", None),
        ("aborted-nonce", "forge"),
    ):
        await _account(account_id, owner)
    async with SessionLocal() as session:
        await FederationRepository(session).abort_peer_transfer(
            "aborted-nonce-12345678901234567890",
            "aborted-nonce",
            AccountTransferDirection.CHECKIN,
            "forge",
            local_instance_id="studio",
        )
    for account_id, owner in (
        ("wrong-owner", "other"),
        ("no-checkout", "forge"),
        ("local-owner", None),
        ("aborted-nonce", "forge"),
    ):
        response = await async_client.post(
            "/api/federation/checkin",
            json={
                "account_id": account_id,
                "nonce": "aborted-nonce-12345678901234567890"
                if account_id == "aborted-nonce"
                else "new-nonce-12345678901234567890",
                "caller_instance_id": "forge",
                "auth": _auth().model_dump(),
            },
            headers={"Authorization": "Bearer checkin-route-credential"},
        )
        assert response.status_code == 409
        assert await _state(account_id) == (
            owner,
            "old-refresh",
            [AccountTransferState.ABORTED] if account_id == "aborted-nonce" else [],
        )


@pytest.mark.asyncio
async def test_checkin_refuses_aborted_checkout_or_wrong_current_owner(
    async_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_LB_FEDERATION_TRANSFER_INBOUND_SHA256", hashlib.sha256(b"checkin-route").hexdigest())
    monkeypatch.setenv("AGENT_LB_FEDERATION_TAKER_INSTANCE_IDS", "forge")
    monkeypatch.setenv("AGENT_LB_LOCAL_INSTANCE_ID", "studio")
    get_settings.cache_clear()
    for account_id, owner, prior_state in (
        ("aborted-checkout", "forge", AccountTransferState.ABORTED),
        ("wrong-checkin-owner", "other", AccountTransferState.SETTLED),
    ):
        await _account(account_id, owner)
        async with SessionLocal() as session:
            repo = FederationRepository(session)
            await repo.create_transfer(
                account_id=account_id,
                direction=AccountTransferDirection.CHECKOUT,
                counterparty_instance_id="forge",
                nonce=f"prior-{account_id}-12345678901234567890",
            )
            if prior_state == AccountTransferState.SETTLED:
                await repo.mark_transfer_settled(f"prior-{account_id}-12345678901234567890")
            else:
                await repo.finish_abort(f"prior-{account_id}-12345678901234567890")
        response = await async_client.post(
            "/api/federation/checkin",
            json={
                "account_id": account_id,
                "nonce": f"checkin-{account_id}-12345678901234567890",
                "caller_instance_id": "forge",
                "auth": _auth().model_dump(),
            },
            headers={"Authorization": "Bearer checkin-route"},
        )
        assert response.status_code == 409
        assert await _state(account_id) == (owner, "old-refresh", [prior_state])


@pytest.mark.asyncio
async def test_checkout_rejects_local_taker_and_reused_nonce(async_client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_LB_FEDERATION_TRANSFER_INBOUND_SHA256", hashlib.sha256(b"route-credential").hexdigest())
    monkeypatch.setenv("AGENT_LB_FEDERATION_TAKER_INSTANCE_IDS", "studio,forge")
    monkeypatch.setenv("AGENT_LB_LOCAL_INSTANCE_ID", "studio")
    get_settings.cache_clear()
    await _account("local-taker", None)
    await _account("reused-nonce", None)
    nonce = "reuse-checkout-nonce-12345678901234567890"
    async with SessionLocal() as session:
        await FederationRepository(session).abort_peer_transfer(
            nonce, "reused-nonce", AccountTransferDirection.CHECKOUT, "forge", local_instance_id="studio"
        )
    for account_id, taker, expected_status, expected_states in (
        ("local-taker", "studio", 403, []),
        ("reused-nonce", "forge", 409, [AccountTransferState.ABORTED]),
    ):
        response = await async_client.post(
            "/api/federation/checkout",
            json={"account_id": account_id, "nonce": nonce, "taker_instance_id": taker},
            headers={"Authorization": "Bearer route-credential"},
        )
        assert response.status_code == expected_status
        assert await _state(account_id) == (None, "old-refresh", expected_states)


@pytest.mark.asyncio
async def test_abort_mismatch_does_not_change_pending_checkout(async_client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_LB_FEDERATION_TRANSFER_INBOUND_SHA256", hashlib.sha256(b"abort-route").hexdigest())
    monkeypatch.setenv("AGENT_LB_FEDERATION_TAKER_INSTANCE_IDS", "forge,other")
    monkeypatch.setenv("AGENT_LB_LOCAL_INSTANCE_ID", "studio")
    get_settings.cache_clear()
    await _account("abort-match", None)
    await _account("abort-other", None)
    nonce = "abort-mismatch-nonce-12345678901234567890"
    checkout = await async_client.post(
        "/api/federation/checkout",
        json={"account_id": "abort-match", "taker_instance_id": "forge", "nonce": nonce},
        headers={"Authorization": "Bearer abort-route"},
    )
    assert checkout.status_code == 200
    before = await _state("abort-match")
    for account_id, direction, caller in (
        ("abort-other", "checkout", "forge"),
        ("abort-match", "checkin", "forge"),
        ("abort-match", "checkout", "other"),
    ):
        response = await async_client.post(
            f"/api/federation/transfers/{nonce}/abort",
            json={"account_id": account_id, "direction": direction, "caller_instance_id": caller},
            headers={"Authorization": "Bearer abort-route"},
        )
        assert response.status_code == 409
        assert await _state("abort-match") == before
        assert await _state("abort-other") == (None, "old-refresh", [])


@pytest.mark.asyncio
async def test_reclaim_requires_outbound_even_with_peer_url(db_setup: bool) -> None:
    del db_setup
    from app.modules.federation.exceptions import FederationNotConfiguredError
    from app.modules.federation.service import FederationService

    await _account("no-outbound", "studio")
    async with SessionLocal() as session:
        await FederationRepository(session).reserve_checkout(
            "no-outbound", "studio", "no-outbound-nonce-12345678901234567890"
        )
    async with SessionLocal() as session:
        service = FederationService(
            FederationRepository(session),
            settings=Settings(
                local_instance_id="forge",
                federation_peer_url="http://studio.invalid",
                federation_transfer_outbound_token=None,
            ),
        )
        with pytest.raises(FederationNotConfiguredError):
            await service.reclaim("no-outbound")
    assert await _state("no-outbound") == ("studio", "old-refresh", [AccountTransferState.PENDING])


@pytest.mark.asyncio
async def test_aborted_checkout_restores_giver_ownership(async_client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_LB_FEDERATION_TRANSFER_INBOUND_SHA256", hashlib.sha256(b"abort-giver").hexdigest())
    monkeypatch.setenv("AGENT_LB_FEDERATION_TAKER_INSTANCE_IDS", "forge")
    monkeypatch.setenv("AGENT_LB_LOCAL_INSTANCE_ID", "studio")
    get_settings.cache_clear()
    await _account("abort-giver", None)
    nonce = "abort-giver-nonce-12345678901234567890"
    headers = {"Authorization": "Bearer abort-giver"}
    checkout = await async_client.post(
        "/api/federation/checkout",
        json={"account_id": "abort-giver", "taker_instance_id": "forge", "nonce": nonce},
        headers=headers,
    )
    assert checkout.status_code == 200
    assert await _state("abort-giver") == ("forge", "old-refresh", [AccountTransferState.PENDING])
    abort = await async_client.post(
        f"/api/federation/transfers/{nonce}/abort",
        json={"account_id": "abort-giver", "direction": "checkout", "caller_instance_id": "forge"},
        headers=headers,
    )
    assert abort.status_code == 200 and abort.json()["state"] == "aborted"
    assert await _state("abort-giver") == ("studio", "old-refresh", [AccountTransferState.ABORTED])
    retry = await async_client.post(
        "/api/federation/checkout",
        json={"account_id": "abort-giver", "taker_instance_id": "forge", "nonce": nonce},
        headers=headers,
    )
    confirm = await async_client.post("/api/federation/checkout/confirm", json={"nonce": nonce}, headers=headers)
    assert (retry.status_code, confirm.status_code) == (409, 404)
    assert await _state("abort-giver") == ("studio", "old-refresh", [AccountTransferState.ABORTED])
    fresh = await async_client.post(
        "/api/federation/checkout",
        json={
            "account_id": "abort-giver",
            "taker_instance_id": "forge",
            "nonce": "fresh-abort-giver-nonce-12345678901234567890",
        },
        headers=headers,
    )
    assert fresh.status_code == 200
    stale_confirm = await async_client.post("/api/federation/checkout/confirm", json={"nonce": nonce}, headers=headers)
    assert stale_confirm.status_code == 404
    assert await _state("abort-giver") == (
        "forge",
        "old-refresh",
        [AccountTransferState.ABORTED, AccountTransferState.PENDING],
    )


@pytest.mark.asyncio
async def test_checkout_and_checkin_refuse_live_exchange_intent(db_setup: bool) -> None:
    del db_setup
    from app.db.models import AccountExchangeIntent
    from app.modules.federation.exceptions import FederationConflictError
    from app.modules.federation.service import FederationService

    await _account("intent-checkout", None)
    await _account("intent-checkin", "forge")
    async with SessionLocal() as session:
        repo = FederationRepository(session)
        await repo.create_transfer(
            account_id="intent-checkin",
            direction=AccountTransferDirection.CHECKOUT,
            counterparty_instance_id="forge",
            nonce="intent-prior-checkout",
        )
        await repo.mark_transfer_settled("intent-prior-checkout")
        from app.modules.accounts.auth_manager import _refresh_token_material_fingerprint

        for account_id in ("intent-checkout", "intent-checkin"):
            account = await session.get(Account, account_id)
            fingerprint = _refresh_token_material_fingerprint(TokenEncryptor(), account.refresh_token_encrypted)
            session.add(
                AccountExchangeIntent(account_id=account_id, refresh_token_sha256=fingerprint, started_at=utcnow())
            )
        await session.commit()
    settings = Settings(local_instance_id="studio", federation_taker_instance_ids=["forge"])
    async with SessionLocal() as session:
        service = FederationService(FederationRepository(session), settings=settings)
        with pytest.raises(FederationConflictError):
            await service.checkout("intent-checkout", "forge", "intent-checkout-nonce-12345678901234567890")
        with pytest.raises(FederationConflictError):
            await service.checkin("intent-checkin", "intent-checkin-nonce-12345678901234567890", "forge", _auth())
    assert await _state("intent-checkout") == (None, "old-refresh", [])
    assert await _state("intent-checkin") == ("forge", "old-refresh", [AccountTransferState.SETTLED])
    async with SessionLocal() as session:
        repo = FederationRepository(session)
        assert (
            await repo.release_for_checkout(
                "intent-checkout",
                "forge",
                local_instance_id="studio",
                nonce="intent-direct-checkout-12345678901234567890",
            )
            is None
        )
        account, _ = await repo.release_for_checkin(
            "intent-checkin", "studio", local_instance_id="forge", nonce="intent-direct-checkin-12345678901234567890"
        )
        assert account is None
    assert await _state("intent-checkout") == (None, "old-refresh", [])
    assert await _state("intent-checkin") == ("forge", "old-refresh", [AccountTransferState.SETTLED])


@pytest.mark.asyncio
async def test_checkin_abort_restores_local_owner_after_peer_rejection(db_setup: bool) -> None:
    del db_setup
    from app.modules.federation.exceptions import FederationConflictError
    from app.modules.federation.service import FederationService

    account_id = "checkin-reclaim-rejection"
    await _account(account_id, "forge")
    async with SessionLocal() as session:
        repo = FederationRepository(session)
        await repo.create_transfer(
            account_id=account_id,
            direction=AccountTransferDirection.CHECKOUT,
            counterparty_instance_id="studio",
            nonce="checkin-reclaim-prior",
        )
        await repo.mark_transfer_settled("checkin-reclaim-prior")

    class Giver:
        checkin_calls = 0
        abort_calls = 0

        async def checkin(self, **kwargs):
            self.checkin_calls += 1
            raise RuntimeError("peer rejected")

        async def abort(self, **kwargs):
            self.abort_calls += 1
            return "aborted"

    giver = Giver()
    async with SessionLocal() as session:
        service = FederationService(
            FederationRepository(session),
            settings=Settings(
                local_instance_id="forge",
                federation_peer_url="http://studio.invalid",
                federation_transfer_outbound_token="outbound-forge",
            ),
            peer_client=giver,
        )
        with pytest.raises(FederationConflictError):
            await service.execute_checkin(account_id)
    assert giver.checkin_calls == giver.abort_calls == 1
    owner, refresh, states = await _state(account_id)
    assert (owner, refresh, set(states)) == (
        "forge",
        "old-refresh",
        {AccountTransferState.SETTLED, AccountTransferState.ABORTED},
    )


@pytest.mark.asyncio
async def test_reclaim_unreachable_aborts_only_after_peer_ack(db_setup: bool) -> None:
    del db_setup
    from app.modules.federation.service import FederationService

    account_id = "unreachable-checkout"
    await _account(account_id, "studio")
    async with SessionLocal() as session:
        await FederationRepository(session).reserve_checkout(
            account_id, "studio", "unreachable-checkout-nonce-12345678901234567890"
        )

    class Giver:
        reachable = False
        abort_calls = 0

        async def abort(self, **kwargs):
            self.abort_calls += 1
            if not self.reachable:
                raise OSError("peer unavailable")
            return "aborted"

    giver = Giver()
    settings = Settings(
        local_instance_id="forge",
        federation_peer_url="http://studio.invalid",
        federation_transfer_outbound_token="outbound-forge",
    )
    async with SessionLocal() as session:
        service = FederationService(FederationRepository(session), settings=settings, peer_client=giver)
        with pytest.raises(OSError):
            await service.reclaim(account_id)
    assert await _state(account_id) == ("studio", "old-refresh", [AccountTransferState.ABORTING])
    giver.reachable = True
    async with SessionLocal() as session:
        service = FederationService(FederationRepository(session), settings=settings, peer_client=giver)
        assert await service.reclaim(account_id)
    assert giver.abort_calls == 2
    assert await _state(account_id) == ("studio", "old-refresh", [AccountTransferState.ABORTED])


@pytest.mark.asyncio
async def test_lost_checkin_response_reports_settled_after_reclaim(db_setup: bool) -> None:
    del db_setup
    from app.modules.federation.peer_client import CheckinPeerResult
    from app.modules.federation.service import FederationService

    account_id = "checkin-lost-response"
    await _account(account_id, "forge")
    async with SessionLocal() as session:
        repo = FederationRepository(session)
        await repo.create_transfer(
            account_id=account_id,
            direction=AccountTransferDirection.CHECKOUT,
            counterparty_instance_id="studio",
            nonce="lost-checkin-prior-checkout",
        )
        await repo.mark_transfer_settled("lost-checkin-prior-checkout")

    class Giver:
        attempts = 0

        async def checkin(self, *, nonce, auth, **kwargs) -> CheckinPeerResult:
            self.attempts += 1
            async with SessionLocal() as session:
                await FederationRepository(session).accept_checkin(
                    account_id, nonce, "forge", auth, encryptor=TokenEncryptor()
                )
            raise OSError("response lost")

        async def abort(self, **kwargs):
            return "settled"

    giver = Giver()
    async with SessionLocal() as session:
        result = await FederationService(
            FederationRepository(session),
            settings=Settings(
                local_instance_id="forge",
                federation_peer_url="http://studio.invalid",
                federation_transfer_outbound_token="outbound-forge",
            ),
            peer_client=giver,
        ).execute_checkin(account_id)
    assert result.settled and giver.attempts == 1
    assert await _state(account_id) == ("studio", "", [AccountTransferState.SETTLED, AccountTransferState.SETTLED])


@pytest.mark.asyncio
@pytest.mark.parametrize("unreachable", [False, True])
async def test_checkin_execute_refusal_returns_reclaim_instruction(
    async_client, app_instance, monkeypatch: pytest.MonkeyPatch, unreachable: bool
) -> None:
    from app.dependencies import FederationContext, get_federation_context
    from app.modules.federation.exceptions import FederationPeerRequestError

    class RefusingService:
        async def execute_checkin(self, account_id: str) -> None:
            assert account_id == "checkin-refused"
            if unreachable:
                raise OSError("peer unavailable")
            raise FederationPeerRequestError("peer refused", status_code=409)

    app_instance.dependency_overrides[get_federation_context] = lambda: FederationContext(
        session=None, repository=None, service=RefusingService()
    )
    try:
        response = await async_client.post("/api/federation/checkin/execute", json={"account_id": "checkin-refused"})
    finally:
        app_instance.dependency_overrides.pop(get_federation_context, None)
    assert response.status_code == (503 if unreachable else 409)
    assert "reclaim checkin-refused" in response.text


@pytest.mark.asyncio
async def test_transfer_auth_requires_allowlist_before_any_checkout(
    async_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_LB_FEDERATION_TRANSFER_INBOUND_SHA256", hashlib.sha256(b"inbound-credential").hexdigest())
    monkeypatch.setenv("AGENT_LB_FEDERATION_TAKER_INSTANCE_IDS", "")
    monkeypatch.setenv("AGENT_LB_LOCAL_INSTANCE_ID", "studio")
    get_settings.cache_clear()
    response = await async_client.post(
        "/api/federation/checkout",
        json={
            "account_id": "no-allowlist",
            "taker_instance_id": "forge",
            "nonce": "no-allowlist-nonce-12345678901234567890",
        },
        headers={"Authorization": "Bearer inbound-credential"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_checkout_refuses_unlisted_taker_before_account_lookup(
    async_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_LB_FEDERATION_TRANSFER_INBOUND_SHA256", hashlib.sha256(b"inbound-credential").hexdigest())
    monkeypatch.setenv("AGENT_LB_FEDERATION_TAKER_INSTANCE_IDS", "forge")
    monkeypatch.setenv("AGENT_LB_LOCAL_INSTANCE_ID", "studio")
    get_settings.cache_clear()
    response = await async_client.post(
        "/api/federation/checkout",
        json={
            "account_id": "not-seeded",
            "taker_instance_id": "unlisted",
            "nonce": "unlisted-nonce-12345678901234567890",
        },
        headers={"Authorization": "Bearer inbound-credential"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("transfer_state", [AccountTransferState.PENDING, AccountTransferState.ABORTING])
async def test_mirror_never_overwrites_open_transfer_token(
    db_setup: bool, transfer_state: AccountTransferState
) -> None:
    del db_setup
    account_id = "mirror-open-transfer"
    await _account(account_id, "studio")
    async with SessionLocal() as session:
        repo = FederationRepository(session)
        transfer = await repo.reserve_checkout(account_id, "studio", "mirror-open-nonce-12345678901234567890")
        transfer.state = transfer_state
        await session.commit()
    async with SessionLocal() as session:
        repo = FederationRepository(session)
        assert not await repo.upsert_mirror_account(
            account_id=account_id,
            provider="anthropic",
            email="custody@example.invalid",
            alias=None,
            status="active",
            plan_type="pro",
            chatgpt_account_id=None,
            access_token="stale",
            owner_instance_id="studio",
            local_instance_id="forge",
            encryptor=TokenEncryptor(),
        )
    assert await _state(account_id) == ("studio", "old-refresh", [transfer_state])
