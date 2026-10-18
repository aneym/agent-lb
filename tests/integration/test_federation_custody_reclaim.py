from __future__ import annotations

import hashlib

import pytest

from app.core.config.settings import Settings, get_settings
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
