from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, FederationUsageDaily, RequestLog
from app.db.session import SessionLocal
from app.modules.federation.repository import FederationRepository
from app.modules.federation.schemas import FederationUsageDayRollup

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
async def test_usage_report_requires_auth_and_upserts_by_instance(
    async_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_federation(monkeypatch)
    payload = {
        "instance_id": "follower-a",
        "rollups": [
            {
                "day": "2026-07-29",
                "account_id": "shared-account",
                "provider": "anthropic",
                "requests": 10,
                "input_tokens": 100,
                "output_tokens": 20,
                "cache_read_tokens": 5,
                "cost": 1.25,
                "session_count": 2,
                "last_request_at": "2026-07-29T12:00:00Z",
            }
        ],
    }

    denied = await async_client.post("/api/federation/usage-report", json=payload)
    assert denied.status_code == 403

    accepted = await async_client.post("/api/federation/usage-report", json=payload, headers=_auth_headers())
    assert accepted.status_code == 200
    payload["rollups"][0]["requests"] = 12
    replaced = await async_client.post("/api/federation/usage-report", json=payload, headers=_auth_headers())
    assert replaced.status_code == 200

    other = dict(payload)
    other["instance_id"] = "follower-b"
    saved_other = await async_client.post("/api/federation/usage-report", json=other, headers=_auth_headers())
    assert saved_other.status_code == 200

    async with SessionLocal() as session:
        rows = (await session.execute(select(FederationUsageDaily))).scalars().all()
    assert len(rows) == 2
    assert {(row.instance_id, row.requests) for row in rows} == {("follower-a", 12), ("follower-b", 12)}


@pytest.mark.asyncio
async def test_usage_report_recovers_from_concurrent_duplicate_insert(monkeypatch: pytest.MonkeyPatch) -> None:
    day = date(2026, 7, 29)
    reported_at = utcnow()
    async with SessionLocal() as session:
        session.add(
            FederationUsageDaily(
                instance_id="racing-follower",
                account_id="shared-account",
                provider="anthropic",
                day=day,
                requests=10,
                input_tokens=100,
                output_tokens=20,
                cache_read_tokens=5,
                cost=1.25,
                session_count=2,
                last_request_at=reported_at,
                reported_at=reported_at,
            )
        )
        await session.commit()
        repository = FederationRepository(session)
        original_get = session.get
        calls = 0

        async def miss_first_get(entity, primary_key):
            nonlocal calls
            calls += 1
            if calls == 1:
                return None
            return await original_get(entity, primary_key)

        monkeypatch.setattr(session, "get", miss_first_get)
        await repository.upsert_usage_report(
            "racing-follower",
            [
                FederationUsageDayRollup(
                    day=day,
                    account_id="shared-account",
                    provider="anthropic",
                    requests=12,
                    input_tokens=120,
                    output_tokens=24,
                    cache_read_tokens=6,
                    cost=1.5,
                    session_count=3,
                    last_request_at=reported_at,
                )
            ],
            reported_at=reported_at,
        )

        row = await original_get(FederationUsageDaily, ("racing-follower", "shared-account", day))
        assert row is not None
        assert row.requests == 12
        assert row.input_tokens == 120
        assert calls >= 2


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
async def test_usage_instances_merges_live_local_and_stored_reports(
    async_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_federation(monkeypatch)
    await _seed_account("usage-account", owner_instance=None)
    now = utcnow()
    async with SessionLocal() as session:
        session.add(
            RequestLog(
                account_id="usage-account",
                provider="anthropic",
                session_id="local-session",
                request_id="local-request",
                requested_at=now,
                model="claude-test",
                input_tokens=20,
                output_tokens=5,
                cache_read_tokens=3,
                cost_usd=0.4,
                status="success",
            )
        )
        session.add(
            FederationUsageDaily(
                instance_id=_LOCAL_INSTANCE_ID,
                account_id="usage-account",
                provider="anthropic",
                day=now.date(),
                requests=999,
                input_tokens=999,
                output_tokens=999,
                cache_read_tokens=999,
                cost=999,
                session_count=999,
                last_request_at=now,
                reported_at=now,
            )
        )
        session.add(
            FederationUsageDaily(
                instance_id="follower-a",
                account_id="usage-account",
                provider="anthropic",
                day=now.date(),
                requests=7,
                input_tokens=70,
                output_tokens=14,
                cache_read_tokens=2,
                cost=0.7,
                session_count=1,
                last_request_at=now,
                reported_at=now,
            )
        )
        await session.commit()

    response = await async_client.get("/api/usage/instances")

    assert response.status_code == 200
    instances = {item["instanceId"]: item for item in response.json()["instances"]}
    assert set(instances) == {_LOCAL_INSTANCE_ID, "follower-a"}
    assert instances[_LOCAL_INSTANCE_ID]["totals"]["requests"] == 1
    assert instances["follower-a"]["totals"]["requests"] == 7


@pytest.mark.asyncio
async def test_local_rollup_groups_mixed_providers_by_account_and_day(
    async_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_federation(monkeypatch)
    await _seed_account("mixed-provider-account", owner_instance=None)
    now = utcnow()
    async with SessionLocal() as session:
        for request_id, provider, input_tokens, cost in (
            ("mixed-anthropic", "anthropic", 20, 0.4),
            ("mixed-openai", "openai", 30, 0.6),
        ):
            session.add(
                RequestLog(
                    account_id="mixed-provider-account",
                    provider=provider,
                    session_id=request_id,
                    request_id=request_id,
                    requested_at=now,
                    model="mixed-test",
                    input_tokens=input_tokens,
                    output_tokens=5,
                    cost_usd=cost,
                    status="success",
                )
            )
        await session.commit()

    response = await async_client.get("/api/usage/instances")

    assert response.status_code == 200
    accounts = response.json()["instances"][0]["days"][0]["accounts"]
    matching = [row for row in accounts if row["accountId"] == "mixed-provider-account"]
    assert len(matching) == 1
    assert matching[0]["provider"] == "openai"
    assert matching[0]["requests"] == 2
    assert matching[0]["inputTokens"] == 50
    assert matching[0]["outputTokens"] == 10
    assert matching[0]["cost"] == pytest.approx(1.0)
    assert matching[0]["sessionCount"] == 2


@pytest.mark.asyncio
async def test_local_rollup_excludes_requests_outside_window(async_client, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_federation(monkeypatch)
    await _seed_account("window-account", owner_instance=None)
    now = utcnow()
    async with SessionLocal() as session:
        for request_id, requested_at in (
            ("recent", now - timedelta(days=2)),
            ("old", now - timedelta(days=30)),
        ):
            session.add(
                RequestLog(
                    account_id="window-account",
                    provider="anthropic",
                    request_id=request_id,
                    requested_at=requested_at,
                    model="claude-test",
                    status="success",
                )
            )
        await session.commit()

    response = await async_client.get("/api/usage/instances")

    assert response.status_code == 200
    local = response.json()["instances"][0]
    assert local["totals"]["requests"] == 1
    assert local["days"][0]["day"] == (date.today() - timedelta(days=2)).isoformat()


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
