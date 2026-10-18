from __future__ import annotations

import hashlib

import pytest

from app.core.config.settings import Settings, get_settings
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountTransferDirection, AccountTransferState
from app.db.session import SessionLocal
from app.modules.federation.repository import FederationRepository
from tests.integration.federation_custody_races_support import (
    _account,
    _auth,
    _state,
)

pytestmark = pytest.mark.integration


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
