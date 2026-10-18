from __future__ import annotations
import pytest
from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.db.models import Account, AccountStatus, AccountTransferDirection, FederationUsageDaily, RequestLog
from app.db.session import SessionLocal
from app.modules.federation.repository import FederationRepository
from tests.integration.federation_api_support import (
    _OTHER_INSTANCE_ID,
    _TAKER_INSTANCE_ID,
    _auth_headers,
    _enable_federation,
    _get_account,
    _seed_account,
    _transfer_headers,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_checkout_happy_path_and_idempotent_retry(async_client, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_federation(monkeypatch)
    await _seed_account("acc_checkout", owner_instance=None, refresh_token="checkout-refresh")

    first = await async_client.post(
        "/api/federation/checkout",
        json={
            "account_id": "acc_checkout",
            "taker_instance_id": _TAKER_INSTANCE_ID,
            "nonce": "checkout-nonce-12345678901234567890",
        },
        headers=_transfer_headers(),
    )
    assert first.status_code == 200
    first_body = first.json()
    assert first_body["auth"]["refresh_token"] == "checkout-refresh"
    assert first_body["nonce"] == "checkout-nonce-12345678901234567890"

    account_after_release = await _get_account("acc_checkout")
    assert account_after_release.owner_instance == _TAKER_INSTANCE_ID

    second = await async_client.post(
        "/api/federation/checkout",
        json={
            "account_id": "acc_checkout",
            "taker_instance_id": _TAKER_INSTANCE_ID,
            "nonce": "checkout-nonce-12345678901234567890",
        },
        headers=_transfer_headers(),
    )
    assert second.status_code == 409
    assert (await _get_account("acc_checkout")).owner_instance == _TAKER_INSTANCE_ID


@pytest.mark.asyncio
async def test_checkout_from_non_owner_returns_409(async_client, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_federation(monkeypatch)
    await _seed_account("acc_conflict", owner_instance=_OTHER_INSTANCE_ID)

    response = await async_client.post(
        "/api/federation/checkout",
        json={
            "account_id": "acc_conflict",
            "taker_instance_id": _TAKER_INSTANCE_ID,
            "nonce": "checkout-nonce-12345678901234567890",
        },
        headers=_transfer_headers(),
    )

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_checkout_confirm_is_idempotent(async_client, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_federation(monkeypatch)
    await _seed_account("acc_confirm", owner_instance=None)

    checkout = await async_client.post(
        "/api/federation/checkout",
        json={
            "account_id": "acc_confirm",
            "taker_instance_id": _TAKER_INSTANCE_ID,
            "nonce": "checkout-nonce-12345678901234567890",
        },
        headers=_transfer_headers(),
    )
    nonce = checkout.json()["nonce"]

    first_confirm = await async_client.post(
        "/api/federation/checkout/confirm", json={"nonce": nonce}, headers=_transfer_headers()
    )
    assert first_confirm.status_code == 200
    assert first_confirm.json()["state"] == "settled"

    second_confirm = await async_client.post(
        "/api/federation/checkout/confirm", json={"nonce": nonce}, headers=_transfer_headers()
    )
    assert second_confirm.status_code == 200
    assert second_confirm.json()["state"] == "settled"


@pytest.mark.asyncio
async def test_checkin_happy_path(async_client, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_federation(monkeypatch)
    await _seed_account("acc_checkin", owner_instance=_TAKER_INSTANCE_ID)
    async with SessionLocal() as session:
        await FederationRepository(session).create_transfer(
            account_id="acc_checkin",
            direction=AccountTransferDirection.CHECKOUT,
            counterparty_instance_id=_TAKER_INSTANCE_ID,
            nonce="prior-checkout-checkin",
        )
        await FederationRepository(session).mark_transfer_settled("prior-checkout-checkin")

    response = await async_client.post(
        "/api/federation/checkin",
        json={
            "account_id": "acc_checkin",
            "nonce": "checkin-nonce-12345678901234567890",
            "caller_instance_id": _TAKER_INSTANCE_ID,
            "auth": {
                "access_token": "rotated-access",
                "refresh_token": "rotated-refresh",
                "id_token": None,
                "expires_at_ms": None,
                "provider": "anthropic",
                "email": "acc_checkin@example.com",
                "alias": "seed-alias",
                "status": "active",
                "plan_type": "claude",
                "chatgpt_account_id": None,
            },
        },
        headers=_transfer_headers(),
    )

    assert response.status_code == 200
    assert response.json()["state"] == "settled"

    account = await _get_account("acc_checkin")
    assert account.owner_instance is None
    encryptor = TokenEncryptor()
    assert encryptor.decrypt(account.access_token_encrypted) == "rotated-access"
    assert encryptor.decrypt(account.refresh_token_encrypted) == "rotated-refresh"


@pytest.mark.asyncio
async def test_checkin_retry_after_success_does_not_reimport(async_client, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_federation(monkeypatch)
    await _seed_account("acc_checkin_retry", owner_instance=_TAKER_INSTANCE_ID)
    async with SessionLocal() as session:
        await FederationRepository(session).create_transfer(
            account_id="acc_checkin_retry",
            direction=AccountTransferDirection.CHECKOUT,
            counterparty_instance_id=_TAKER_INSTANCE_ID,
            nonce="prior-checkout-retry",
        )
        await FederationRepository(session).mark_transfer_settled("prior-checkout-retry")

    payload_a = {
        "account_id": "acc_checkin_retry",
        "nonce": "checkin-nonce-retry-12345678901234567890",
        "caller_instance_id": _TAKER_INSTANCE_ID,
        "auth": {
            "access_token": "payload-a-access",
            "refresh_token": "payload-a-refresh",
            "id_token": None,
            "expires_at_ms": None,
            "provider": "anthropic",
            "email": "acc_checkin_retry@example.com",
            "alias": None,
            "status": "active",
            "plan_type": "claude",
            "chatgpt_account_id": None,
        },
    }
    first = await async_client.post("/api/federation/checkin", json=payload_a, headers=_transfer_headers())
    assert first.status_code == 200
    assert first.json()["state"] == "settled"

    account_after_first = await _get_account("acc_checkin_retry")
    encryptor = TokenEncryptor()
    assert encryptor.decrypt(account_after_first.access_token_encrypted) == "payload-a-access"

    payload_b = dict(payload_a)
    payload_b["auth"] = dict(payload_a["auth"])
    payload_b["auth"]["access_token"] = "payload-b-access-should-not-apply"

    second = await async_client.post("/api/federation/checkin", json=payload_b, headers=_transfer_headers())
    assert second.status_code == 200
    assert second.json()["state"] == "settled"

    account_after_second = await _get_account("acc_checkin_retry")
    assert encryptor.decrypt(account_after_second.access_token_encrypted) == "payload-a-access"


@pytest.mark.asyncio
async def test_legacy_mirror_credential_cannot_transfer_and_checkout_requires_allowed_taker(
    async_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_federation(monkeypatch)
    await _seed_account("scoped-account", owner_instance=None)
    for route, payload in (
        (
            "checkout",
            {
                "account_id": "scoped-account",
                "taker_instance_id": _TAKER_INSTANCE_ID,
                "nonce": "checkout-nonce-12345678901234567890",
            },
        ),
        ("checkout/confirm", {"nonce": "arbitrary"}),
        ("checkin", {}),
        (
            "transfers/arbitrary/abort",
            {"account_id": "scoped-account", "direction": "checkout", "caller_instance_id": _TAKER_INSTANCE_ID},
        ),
    ):
        denied = await async_client.post(f"/api/federation/{route}", json=payload, headers=_auth_headers())
        assert denied.status_code == 403, route
    denied_taker = await async_client.post(
        "/api/federation/checkout",
        json={
            "account_id": "scoped-account",
            "taker_instance_id": _OTHER_INSTANCE_ID,
            "nonce": "checkout-nonce-12345678901234567890",
        },
        headers=_transfer_headers(),
    )
    assert denied_taker.status_code == 403
    assert (await _get_account("scoped-account")).owner_instance is None

    monkeypatch.delenv("AGENT_LB_FEDERATION_TRANSFER_INBOUND_SHA256")
    get_settings.cache_clear()
    legacy_denied = await async_client.post(
        "/api/federation/checkout",
        json={
            "account_id": "scoped-account",
            "taker_instance_id": _TAKER_INSTANCE_ID,
            "nonce": "checkout-nonce-12345678901234567890",
        },
        headers=_auth_headers(),
    )
    assert legacy_denied.status_code == 403
    assert (await async_client.get("/api/federation/mirror", headers=_auth_headers())).status_code == 200


@pytest.mark.asyncio
async def test_confirm_blanks_old_refresh_only_after_confirmation(
    async_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_federation(monkeypatch)
    await _seed_account("confirmed-account", owner_instance=None, refresh_token="handoff-refresh")
    checkout = await async_client.post(
        "/api/federation/checkout",
        json={
            "account_id": "confirmed-account",
            "taker_instance_id": _TAKER_INSTANCE_ID,
            "nonce": "checkout-nonce-12345678901234567890",
        },
        headers=_transfer_headers(),
    )
    assert checkout.status_code == 200
    encryptor = TokenEncryptor()
    assert encryptor.decrypt((await _get_account("confirmed-account")).refresh_token_encrypted) == "handoff-refresh"
    nonce = checkout.json()["nonce"]
    assert (
        await async_client.post("/api/federation/checkout/confirm", json={"nonce": nonce}, headers=_transfer_headers())
    ).status_code == 200
    assert encryptor.decrypt((await _get_account("confirmed-account")).refresh_token_encrypted) == ""
    assert (
        await async_client.post("/api/federation/checkout/confirm", json={"nonce": nonce}, headers=_transfer_headers())
    ).status_code == 200


@pytest.mark.asyncio
async def test_reclaim_only_unsettled_handoff(async_client, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_federation(monkeypatch)
    await _seed_account("reclaim-account", owner_instance=None)
    checkout = await async_client.post(
        "/api/federation/checkout",
        json={
            "account_id": "reclaim-account",
            "taker_instance_id": _TAKER_INSTANCE_ID,
            "nonce": "reclaim-nonce-12345678901234567890",
        },
        headers=_transfer_headers(),
    )
    nonce = checkout.json()["nonce"]

    assert (await async_client.post("/api/federation/reclaim/reclaim-account", json={})).status_code == 503
    assert (await _get_account("reclaim-account")).owner_instance == _TAKER_INSTANCE_ID
    assert (
        await async_client.post("/api/federation/checkout/confirm", json={"nonce": nonce}, headers=_transfer_headers())
    ).status_code == 200
    retry = await async_client.post(
        "/api/federation/checkout",
        json={
            "account_id": "reclaim-account",
            "taker_instance_id": _TAKER_INSTANCE_ID,
            "nonce": "reclaim-nonce-12345678901234567890",
        },
        headers=_transfer_headers(),
    )
    assert retry.status_code == 409
    assert (await async_client.post("/api/federation/reclaim/reclaim-account", json={})).status_code == 503
    assert (await _get_account("reclaim-account")).owner_instance == _TAKER_INSTANCE_ID
