from __future__ import annotations

import pytest

from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal

pytestmark = pytest.mark.integration

_FEDERATION_TOKEN = "peer-secret-token"
_LOCAL_INSTANCE_ID = "studio-test"
_TAKER_INSTANCE_ID = "laptop-test"
_OTHER_INSTANCE_ID = "other-instance-test"


def _enable_federation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_LB_FEDERATION_TOKEN", _FEDERATION_TOKEN)
    monkeypatch.setenv("AGENT_LB_LOCAL_INSTANCE_ID", _LOCAL_INSTANCE_ID)
    get_settings.cache_clear()


def _auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_FEDERATION_TOKEN}"}


async def _seed_account(
    account_id: str,
    *,
    owner_instance: str | None,
    access_token: str = "seed-access",
    refresh_token: str = "seed-refresh",
) -> None:
    encryptor = TokenEncryptor()
    account = Account(
        id=account_id,
        provider="anthropic",
        chatgpt_account_id=None,
        email=f"{account_id}@example.com",
        alias="seed-alias",
        plan_type="claude",
        access_token_encrypted=encryptor.encrypt(access_token),
        refresh_token_encrypted=encryptor.encrypt(refresh_token),
        id_token_encrypted=None,
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )
    account.owner_instance = owner_instance
    async with SessionLocal() as session:
        session.add(account)
        await session.commit()


async def _get_account(account_id: str) -> Account:
    async with SessionLocal() as session:
        account = await session.get(Account, account_id)
        assert account is not None
        return account


@pytest.mark.asyncio
async def test_mirror_requires_bearer_auth(async_client, monkeypatch: pytest.MonkeyPatch) -> None:
    # Federation entirely off (no token configured): 403.
    response = await async_client.get("/api/federation/mirror")
    assert response.status_code == 403

    _enable_federation(monkeypatch)

    # Missing bearer credentials: 403.
    response = await async_client.get("/api/federation/mirror")
    assert response.status_code == 403

    # Wrong bearer token: 403.
    response = await async_client.get("/api/federation/mirror", headers={"Authorization": "Bearer wrong-token"})
    assert response.status_code == 403

    # Correct token: 200.
    response = await async_client.get("/api/federation/mirror", headers=_auth_headers())
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_mirror_never_includes_refresh_tokens(async_client, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_federation(monkeypatch)
    await _seed_account("acc_mirror_owned", owner_instance=None, refresh_token="super-secret-refresh")

    response = await async_client.get("/api/federation/mirror", headers=_auth_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["instance_id"] == _LOCAL_INSTANCE_ID
    body_text = response.text
    assert "super-secret-refresh" not in body_text
    assert "refresh_token" not in payload
    for account_payload in payload["accounts"]:
        assert "refresh_token" not in account_payload
    matched = [a for a in payload["accounts"] if a["account_id"] == "acc_mirror_owned"]
    assert len(matched) == 1
    assert matched[0]["access_token"] == "seed-access"


@pytest.mark.asyncio
async def test_checkout_happy_path_and_idempotent_retry(async_client, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_federation(monkeypatch)
    await _seed_account("acc_checkout", owner_instance=None, refresh_token="checkout-refresh")

    first = await async_client.post(
        "/api/federation/checkout",
        json={"account_id": "acc_checkout", "taker_instance_id": _TAKER_INSTANCE_ID},
        headers=_auth_headers(),
    )
    assert first.status_code == 200
    first_body = first.json()
    assert first_body["auth"]["refresh_token"] == "checkout-refresh"
    nonce = first_body["nonce"]

    account_after_release = await _get_account("acc_checkout")
    assert account_after_release.owner_instance == _TAKER_INSTANCE_ID

    # Retry (lost response): same taker, same nonce, same payload — no double transfer.
    second = await async_client.post(
        "/api/federation/checkout",
        json={"account_id": "acc_checkout", "taker_instance_id": _TAKER_INSTANCE_ID},
        headers=_auth_headers(),
    )
    assert second.status_code == 200
    second_body = second.json()
    assert second_body["nonce"] == nonce
    assert second_body["auth"]["refresh_token"] == "checkout-refresh"


@pytest.mark.asyncio
async def test_checkout_from_non_owner_returns_409(async_client, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_federation(monkeypatch)
    await _seed_account("acc_conflict", owner_instance=_OTHER_INSTANCE_ID)

    response = await async_client.post(
        "/api/federation/checkout",
        json={"account_id": "acc_conflict", "taker_instance_id": _TAKER_INSTANCE_ID},
        headers=_auth_headers(),
    )

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_checkout_confirm_is_idempotent(async_client, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_federation(monkeypatch)
    await _seed_account("acc_confirm", owner_instance=None)

    checkout = await async_client.post(
        "/api/federation/checkout",
        json={"account_id": "acc_confirm", "taker_instance_id": _TAKER_INSTANCE_ID},
        headers=_auth_headers(),
    )
    nonce = checkout.json()["nonce"]

    first_confirm = await async_client.post(
        "/api/federation/checkout/confirm", json={"nonce": nonce}, headers=_auth_headers()
    )
    assert first_confirm.status_code == 200
    assert first_confirm.json()["state"] == "settled"

    second_confirm = await async_client.post(
        "/api/federation/checkout/confirm", json={"nonce": nonce}, headers=_auth_headers()
    )
    assert second_confirm.status_code == 200
    assert second_confirm.json()["state"] == "settled"


@pytest.mark.asyncio
async def test_checkin_happy_path(async_client, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_federation(monkeypatch)
    await _seed_account("acc_checkin", owner_instance=_TAKER_INSTANCE_ID)

    response = await async_client.post(
        "/api/federation/checkin",
        json={
            "account_id": "acc_checkin",
            "nonce": "checkin-nonce-1",
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
        headers=_auth_headers(),
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

    payload_a = {
        "account_id": "acc_checkin_retry",
        "nonce": "checkin-nonce-retry",
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
    first = await async_client.post("/api/federation/checkin", json=payload_a, headers=_auth_headers())
    assert first.status_code == 200
    assert first.json()["state"] == "settled"

    account_after_first = await _get_account("acc_checkin_retry")
    encryptor = TokenEncryptor()
    assert encryptor.decrypt(account_after_first.access_token_encrypted) == "payload-a-access"

    # Deliberately different payload on "retry" with the same nonce — proves
    # the second call is a no-op lookup, not a re-import, since a real T
    # retry would resend identical content anyway (its gate stayed closed).
    payload_b = dict(payload_a)
    payload_b["auth"] = dict(payload_a["auth"])
    payload_b["auth"]["access_token"] = "payload-b-access-should-not-apply"

    second = await async_client.post("/api/federation/checkin", json=payload_b, headers=_auth_headers())
    assert second.status_code == 200
    assert second.json()["state"] == "settled"

    account_after_second = await _get_account("acc_checkin_retry")
    assert encryptor.decrypt(account_after_second.access_token_encrypted) == "payload-a-access"
