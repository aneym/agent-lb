from __future__ import annotations
import hashlib
import pytest
from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, AccountTransferDirection, FederationUsageDaily, RequestLog
from app.db.session import SessionLocal
from app.modules.federation.repository import FederationRepository
from tests.integration.federation_api_support import (
    _FEDERATION_TOKEN,
    _LOCAL_INSTANCE_ID,
    _TAKER_INSTANCE_ID,
    _TRANSFER_TOKEN,
    _auth_headers,
    _enable_federation,
    _get_account,
    _seed_account,
    _transfer_headers,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_mirror_requires_bearer_auth(async_client, monkeypatch: pytest.MonkeyPatch) -> None:

    response = await async_client.get("/api/federation/mirror")
    assert response.status_code == 403

    _enable_federation(monkeypatch)

    response = await async_client.get("/api/federation/mirror")
    assert response.status_code == 403

    response = await async_client.get("/api/federation/mirror", headers={"Authorization": "Bearer wrong-token"})
    assert response.status_code == 403

    response = await async_client.get("/api/federation/mirror", headers=_auth_headers())
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_status_works_unconfigured_and_never_exposes_token(async_client) -> None:
    response = await async_client.get("/api/federation/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["peerUrl"] is None
    assert payload["mirror"]["enabled"] is False
    assert "federationToken" not in response.text
    assert "peer-secret-token" not in response.text


@pytest.mark.asyncio
async def test_status_reports_healthy_follower(async_client, app_instance, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_LB_FEDERATION_TOKEN", _FEDERATION_TOKEN)
    monkeypatch.setenv("AGENT_LB_FEDERATION_PEER_URL", "https://studio.example")
    monkeypatch.setenv("AGENT_LB_LOCAL_INSTANCE_ID", _LOCAL_INSTANCE_ID)
    get_settings.cache_clear()
    await _seed_account("mirrored-status-account", owner_instance="studio-owner")
    scheduler = app_instance.state.federation_mirror_scheduler
    scheduler.last_success_at = utcnow()
    scheduler.consecutive_failures = 0

    response = await async_client.get("/api/federation/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["localInstanceId"] == _LOCAL_INSTANCE_ID
    assert payload["peerUrl"] == "https://studio.example"
    assert payload["mirror"]["enabled"] is True
    assert payload["mirror"]["lastSuccessAt"] is not None
    assert payload["mirror"]["consecutiveFailures"] == 0
    assert payload["accounts"]["mirrored"] > 0


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
async def test_pending_checkout_mirror_keeps_retryable_token(async_client, monkeypatch: pytest.MonkeyPatch) -> None:

    _enable_federation(monkeypatch)
    await _seed_account("pending-mirror", owner_instance=None, refresh_token="still-live")
    first = await async_client.post(
        "/api/federation/checkout",
        json={
            "account_id": "pending-mirror",
            "taker_instance_id": _TAKER_INSTANCE_ID,
            "nonce": "pending-mirror-nonce-12345678901234567890",
        },
        headers=_transfer_headers(),
    )
    assert first.status_code == 200
    async with SessionLocal() as session:
        assert not await FederationRepository(session).upsert_mirror_account(
            account_id="pending-mirror",
            provider="anthropic",
            email="pending-mirror@example.com",
            alias=None,
            status="active",
            plan_type="claude",
            chatgpt_account_id=None,
            access_token="mirror-access",
            owner_instance_id=_TAKER_INSTANCE_ID,
            local_instance_id=_LOCAL_INSTANCE_ID,
            encryptor=TokenEncryptor(),
        )
    retry = await async_client.post(
        "/api/federation/checkout",
        json={
            "account_id": "pending-mirror",
            "taker_instance_id": _TAKER_INSTANCE_ID,
            "nonce": "pending-mirror-nonce-12345678901234567890",
        },
        headers=_transfer_headers(),
    )
    assert retry.status_code == 409
    assert TokenEncryptor().decrypt((await _get_account("pending-mirror")).refresh_token_encrypted) == "still-live"


@pytest.mark.asyncio
async def test_outbound_transfer_token_does_not_enable_inbound_routes(
    async_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_federation(monkeypatch)
    monkeypatch.setenv("AGENT_LB_FEDERATION_MIRROR_TOKEN", "dedicated-mirror")
    monkeypatch.setenv("AGENT_LB_FEDERATION_TAKER_INSTANCE_IDS", "")
    get_settings.cache_clear()
    for token in (_TRANSFER_TOKEN, "dedicated-mirror", _FEDERATION_TOKEN):
        denied = await async_client.post(
            "/api/federation/checkin",
            json={},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert denied.status_code == 403

    monkeypatch.setenv("AGENT_LB_FEDERATION_TAKER_INSTANCE_IDS", _TAKER_INSTANCE_ID)
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="federation_transfer_inbound_sha256 must differ"):
        from app.core.config.settings import Settings

        Settings(
            federation_token=_FEDERATION_TOKEN,
            federation_transfer_inbound_sha256=hashlib.sha256(_FEDERATION_TOKEN.encode()).hexdigest(),
        )


@pytest.mark.asyncio
async def test_mirror_snapshot_cannot_overwrite_concurrent_local_checkout(db_setup: bool) -> None:

    from sqlalchemy import update

    if SessionLocal.kw["bind"].dialect.name != "postgresql":
        pytest.skip("Requires concurrent transactions on disposable Postgres")
    await _seed_account("mirror-owner-race", owner_instance="peer", refresh_token="local-live")
    encryptor = TokenEncryptor()
    async with SessionLocal() as mirror_session:
        await mirror_session.get(Account, "mirror-owner-race")
        async with SessionLocal() as owner_session:
            await owner_session.execute(
                update(Account).where(Account.id == "mirror-owner-race").values(owner_instance=_LOCAL_INSTANCE_ID)
            )
            await owner_session.commit()
        applied = await FederationRepository(mirror_session).upsert_mirror_account(
            account_id="mirror-owner-race",
            provider="anthropic",
            email="mirror-owner-race@example.com",
            alias=None,
            status="active",
            plan_type="claude",
            chatgpt_account_id=None,
            access_token="stale-mirror",
            owner_instance_id="peer",
            local_instance_id=_LOCAL_INSTANCE_ID,
            encryptor=encryptor,
        )
        assert applied is False
    current = await _get_account("mirror-owner-race")
    assert current.owner_instance == _LOCAL_INSTANCE_ID
    assert encryptor.decrypt(current.refresh_token_encrypted) == "local-live"
