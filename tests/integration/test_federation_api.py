from __future__ import annotations

import hashlib
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, AccountTransferDirection, FederationUsageDaily, RequestLog
from app.db.session import SessionLocal
from app.modules.federation.repository import FederationRepository
from app.modules.federation.schemas import FederationUsageDayRollup

pytestmark = pytest.mark.integration

_FEDERATION_TOKEN = "peer-secret-token"
_TRANSFER_TOKEN = "transfer-test-token"
_LOCAL_INSTANCE_ID = "studio-test"
_TAKER_INSTANCE_ID = "laptop-test"
_OTHER_INSTANCE_ID = "other-instance-test"


def _enable_federation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_LB_FEDERATION_TOKEN", _FEDERATION_TOKEN)
    monkeypatch.setenv(
        "AGENT_LB_FEDERATION_TRANSFER_INBOUND_SHA256", hashlib.sha256(_TRANSFER_TOKEN.encode()).hexdigest()
    )
    monkeypatch.setenv("AGENT_LB_FEDERATION_TAKER_INSTANCE_IDS", _TAKER_INSTANCE_ID)
    monkeypatch.setenv("AGENT_LB_LOCAL_INSTANCE_ID", _LOCAL_INSTANCE_ID)
    get_settings.cache_clear()


def _auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_FEDERATION_TOKEN}"}


def _transfer_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_TRANSFER_TOKEN}"}


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

    # Retry (lost response): same taker, same nonce, same payload — no double transfer.
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

    # Deliberately different payload on "retry" with the same nonce — proves
    # the second call is a no-op lookup, not a re-import, since a real T
    # retry would resend identical content anyway (its gate stayed closed).
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
    # The answering side has no outbound credential and cannot roll back itself.
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


@pytest.mark.asyncio
async def test_postgres_checkout_crash_before_and_after_commit_retries_without_double_ownership(
    db_setup: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    del db_setup
    from app.core.config.settings import Settings
    from app.db.models import AccountTransfer, AccountTransferDirection
    from app.modules.federation.exceptions import FederationConflictError
    from app.modules.federation.service import FederationService

    if SessionLocal.kw["bind"].dialect.name != "postgresql":
        pytest.skip("Requires disposable Postgres to verify durable transaction boundaries")
    await _seed_account("checkout-crash", owner_instance=None, refresh_token="live-refresh")
    settings = Settings(local_instance_id=_LOCAL_INSTANCE_ID)
    encryptor = TokenEncryptor()
    retry_nonce = ""
    for crash_before_commit in (True, False):
        # The first attempt crashes either before the only commit or just
        # after it; a new session models the next process on retry.
        async with SessionLocal() as session:
            repo = FederationRepository(session)
            original_commit = session.commit

            async def crash_commit() -> None:
                if crash_before_commit:
                    await session.flush()
                    raise RuntimeError("process interrupted before commit")
                await original_commit()
                raise RuntimeError("response lost after commit")

            monkeypatch.setattr(session, "commit", crash_commit)
            with pytest.raises(RuntimeError, match="interrupted|response lost"):
                await FederationService(repo, settings=settings, encryptor=encryptor).checkout(
                    "checkout-crash", _TAKER_INSTANCE_ID, "crash-nonce-12345678901234567890"
                )
        async with SessionLocal() as session:
            repo = FederationRepository(session)
            if crash_before_commit:
                assert (await repo.get_account("checkout-crash")).owner_instance is None
            if crash_before_commit:
                retry = await FederationService(repo, settings=settings, encryptor=encryptor).checkout(
                    "checkout-crash", _TAKER_INSTANCE_ID, "crash-retry-nonce-12345678901234567890"
                )
                assert retry.auth.refresh_token == "live-refresh"
            else:
                with pytest.raises(FederationConflictError):
                    await FederationService(repo, settings=settings, encryptor=encryptor).checkout(
                        "checkout-crash", _TAKER_INSTANCE_ID, "crash-nonce-12345678901234567890"
                    )
                retry_nonce = "crash-nonce-12345678901234567890"
            rows = (
                (
                    await session.execute(
                        select(AccountTransfer).where(
                            AccountTransfer.account_id == "checkout-crash",
                            AccountTransfer.direction == AccountTransferDirection.CHECKOUT,
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(rows) == 1
            assert rows[0].nonce == (retry.nonce if crash_before_commit else retry_nonce)
            assert (await repo.get_account("checkout-crash")).owner_instance == _TAKER_INSTANCE_ID
        if crash_before_commit:
            # Prepare the second scenario from the pristine state again.
            async with SessionLocal() as session:
                from sqlalchemy import delete

                await session.execute(delete(AccountTransfer).where(AccountTransfer.account_id == "checkout-crash"))
                (await session.get(Account, "checkout-crash")).owner_instance = None
                await session.commit()

    async with SessionLocal() as session:
        service = FederationService(FederationRepository(session), settings=settings, encryptor=encryptor)
        await service.confirm_checkout(retry_nonce)
    assert encryptor.decrypt((await _get_account("checkout-crash")).refresh_token_encrypted) == ""


@pytest.mark.asyncio
async def test_postgres_checkin_crash_before_commit_preserves_owner_and_old_token(
    db_setup: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    del db_setup
    from app.core.config.settings import Settings
    from app.db.models import AccountTransfer, AccountTransferDirection
    from app.modules.federation.schemas import FederationAuthPayload
    from app.modules.federation.service import FederationService

    if SessionLocal.kw["bind"].dialect.name != "postgresql":
        pytest.skip("Requires disposable Postgres")
    await _seed_account("checkin-crash", owner_instance=_TAKER_INSTANCE_ID)
    async with SessionLocal() as session:
        repo = FederationRepository(session)
        await repo.create_transfer(
            account_id="checkin-crash",
            direction=AccountTransferDirection.CHECKOUT,
            counterparty_instance_id=_TAKER_INSTANCE_ID,
            nonce="prior-checkout-crash",
        )
        await repo.mark_transfer_settled("prior-checkout-crash")
    payload = FederationAuthPayload(
        access_token="new-access",
        refresh_token="new-refresh",
        id_token=None,
        expires_at_ms=None,
        provider="anthropic",
        email="checkin-crash@example.com",
        alias=None,
        status="active",
        plan_type="claude",
        chatgpt_account_id=None,
    )
    async with SessionLocal() as session:

        async def crash_commit() -> None:
            await session.flush()
            raise RuntimeError("process interrupted before commit")

        monkeypatch.setattr(session, "commit", crash_commit)
        with pytest.raises(RuntimeError, match="interrupted"):
            await FederationService(
                FederationRepository(session),
                settings=Settings(
                    local_instance_id=_LOCAL_INSTANCE_ID, federation_taker_instance_ids=[_TAKER_INSTANCE_ID]
                ),
            ).checkin("checkin-crash", "return-nonce-12345678901234567890", _TAKER_INSTANCE_ID, payload)
    account = await _get_account("checkin-crash")
    assert account.owner_instance == _TAKER_INSTANCE_ID
    assert TokenEncryptor().decrypt(account.refresh_token_encrypted) == "seed-refresh"
    # On the pre-fix path the first commit happens *before* import, so this
    # assertion exposes the real crash window rather than passing vacuously.
    async with SessionLocal() as session:
        rows = (
            (
                await session.execute(
                    select(AccountTransfer).where(
                        AccountTransfer.account_id == "checkin-crash",
                        AccountTransfer.direction == AccountTransferDirection.CHECKIN,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert rows == []
        await FederationService(
            FederationRepository(session),
            settings=Settings(local_instance_id=_LOCAL_INSTANCE_ID, federation_taker_instance_ids=[_TAKER_INSTANCE_ID]),
        ).checkin("checkin-crash", "return-nonce-12345678901234567890", _TAKER_INSTANCE_ID, payload)
    account = await _get_account("checkin-crash")
    assert account.owner_instance is None
    assert TokenEncryptor().decrypt(account.refresh_token_encrypted) == "new-refresh"


@pytest.mark.asyncio
async def test_pending_checkout_mirror_keeps_retryable_token(async_client, monkeypatch: pytest.MonkeyPatch) -> None:
    """The mirror is not the confirmation; only confirmation erases the exported token."""
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
    # A renamed legacy mirror credential must not become a transfer credential.
    monkeypatch.setenv("AGENT_LB_FEDERATION_TAKER_INSTANCE_IDS", _TAKER_INSTANCE_ID)
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="federation_transfer_inbound_sha256 must differ"):
        from app.core.config.settings import Settings

        Settings(
            federation_token=_FEDERATION_TOKEN,
            federation_transfer_inbound_sha256=hashlib.sha256(_FEDERATION_TOKEN.encode()).hexdigest(),
        )


@pytest.mark.asyncio
async def test_postgres_checkin_has_no_committed_import_before_owner_change(
    db_setup: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crash on the old second commit cannot expose a new token with the old owner."""
    from app.core.config.settings import Settings
    from app.db.models import AccountTransferDirection
    from app.modules.federation.schemas import FederationAuthPayload
    from app.modules.federation.service import FederationService

    if SessionLocal.kw["bind"].dialect.name != "postgresql":
        pytest.skip("Requires disposable Postgres")
    await _seed_account("checkin-one-commit", owner_instance=_TAKER_INSTANCE_ID)
    async with SessionLocal() as session:
        repo = FederationRepository(session)
        await repo.create_transfer(
            account_id="checkin-one-commit",
            direction=AccountTransferDirection.CHECKOUT,
            counterparty_instance_id=_TAKER_INSTANCE_ID,
            nonce="prior-checkout-one-commit",
        )
        await repo.mark_transfer_settled("prior-checkout-one-commit")
    payload = FederationAuthPayload(
        access_token="new-access",
        refresh_token="new-refresh",
        id_token=None,
        expires_at_ms=None,
        provider="anthropic",
        email="checkin-one-commit@example.com",
        alias=None,
        status="active",
        plan_type="claude",
        chatgpt_account_id=None,
    )
    async with SessionLocal() as session:
        original_commit = session.commit
        commits = 0

        async def fail_on_second_commit() -> None:
            nonlocal commits
            commits += 1
            if commits == 2:
                await session.flush()
                raise RuntimeError("interrupted between import and owner change")
            await original_commit()

        monkeypatch.setattr(session, "commit", fail_on_second_commit)
        try:
            await FederationService(
                FederationRepository(session),
                settings=Settings(
                    local_instance_id=_LOCAL_INSTANCE_ID, federation_taker_instance_ids=[_TAKER_INSTANCE_ID]
                ),
            ).checkin("checkin-one-commit", "single-nonce-12345678901234567890", _TAKER_INSTANCE_ID, payload)
        except RuntimeError:
            pass
    account = await _get_account("checkin-one-commit")
    assert not (
        account.owner_instance == _TAKER_INSTANCE_ID
        and TokenEncryptor().decrypt(account.refresh_token_encrypted) == "new-refresh"
    )
    assert account.owner_instance is None
    assert TokenEncryptor().decrypt(account.refresh_token_encrypted) == "new-refresh"


@pytest.mark.asyncio
async def test_mirror_snapshot_cannot_overwrite_concurrent_local_checkout(db_setup: bool) -> None:
    """A stale mirror read must not blank a token after the owner changes."""
    from sqlalchemy import update

    if SessionLocal.kw["bind"].dialect.name != "postgresql":
        pytest.skip("Requires concurrent transactions on disposable Postgres")
    await _seed_account("mirror-owner-race", owner_instance="peer", refresh_token="local-live")
    encryptor = TokenEncryptor()
    async with SessionLocal() as mirror_session:
        # Hold an old identity-map snapshot while another transaction takes
        # ownership, then exercise the real mirror update with that session.
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


def _request_log_row(source_row_id: int, request_id: str, account_id: str | None) -> dict[str, object]:
    return {
        "source_row_id": source_row_id,
        "account_id": account_id,
        "provider": "anthropic",
        "api_key_id": "edge-local-key",
        "request_id": request_id,
        "request_kind": "normal",
        "requested_at": "2026-10-03T15:00:00Z",
        "model": "claude-opus",
        "status": "success",
        "room": "lab",
        "caller_seat": "alex",
        "unified_5h_utilization": 0.25,
        "unified_7d_utilization": 0.5,
        "input_tokens": 11,
    }


@pytest.mark.asyncio
async def test_request_logs_ingest_is_idempotent_and_requires_mirror_auth(async_client, monkeypatch) -> None:
    _enable_federation(monkeypatch)
    await _seed_account("known-acct", owner_instance=None)
    payload = {
        "instance_id": "ax42",
        "rows": [
            _request_log_row(11, "req-known", "known-acct"),
            _request_log_row(12, "req-unknown", "missing-acct"),
        ],
    }

    denied = await async_client.post("/api/federation/request-logs", json=payload)
    assert denied.status_code == 403
    wrong = await async_client.post(
        "/api/federation/request-logs", json=payload, headers={"Authorization": "Bearer wrong-token"}
    )
    assert wrong.status_code == 403

    accepted = await async_client.post("/api/federation/request-logs", json=payload, headers=_auth_headers())
    assert accepted.status_code == 200
    assert accepted.json() == {"accepted": 2, "skipped": 0, "max_source_row_id": 12}

    again = await async_client.post("/api/federation/request-logs", json=payload, headers=_auth_headers())
    assert again.status_code == 200
    assert again.json() == {"accepted": 0, "skipped": 2, "max_source_row_id": 12}

    async with SessionLocal() as session:
        rows = (await session.execute(select(RequestLog).order_by(RequestLog.request_id))).scalars().all()
    assert len(rows) == 2
    by_request = {row.request_id: row for row in rows}
    known = by_request["req-known"]
    unknown = by_request["req-unknown"]
    assert known.source == "edge:ax42"
    assert unknown.source == "edge:ax42"
    assert known.api_key_id is None
    assert unknown.api_key_id is None
    assert known.account_id == "known-acct"
    assert unknown.account_id is None
    assert known.room == "lab"
    assert known.caller_seat == "alex"
    assert known.unified_5h_utilization == 0.25
    assert known.unified_7d_utilization == 0.5
    assert known.input_tokens == 11


@pytest.mark.asyncio
async def test_request_logs_rejects_batches_over_500(async_client, monkeypatch) -> None:
    _enable_federation(monkeypatch)
    rows = [_request_log_row(index, f"bulk-{index}", None) for index in range(1, 502)]
    response = await async_client.post(
        "/api/federation/request-logs",
        json={"instance_id": "ax42", "rows": rows},
        headers=_auth_headers(),
    )
    assert response.status_code in {413, 422}


@pytest.mark.asyncio
async def test_list_local_usage_rollups_excludes_edge_rows(db_setup) -> None:
    del db_setup
    now = utcnow()
    async with SessionLocal() as session:
        session.add(
            Account(
                id="rollup-acct",
                provider="anthropic",
                email="rollup-acct@example.com",
                plan_type="claude",
                access_token_encrypted=b"access",
                refresh_token_encrypted=b"refresh",
                last_refresh=now,
                status=AccountStatus.ACTIVE,
            )
        )
        session.add(
            RequestLog(
                account_id="rollup-acct",
                request_id="local-row",
                model="claude-opus",
                status="success",
                requested_at=now,
                input_tokens=5,
                source=None,
            )
        )
        session.add(
            RequestLog(
                account_id="rollup-acct",
                request_id="edge-row",
                model="claude-opus",
                status="success",
                requested_at=now,
                input_tokens=9,
                source="edge:ax42",
            )
        )
        await session.commit()
        rollups = await FederationRepository(session).list_local_usage_rollups(window_days=7)
    assert len(rollups) == 1
    assert rollups[0].account_id == "rollup-acct"
    assert rollups[0].requests == 1
    assert rollups[0].input_tokens == 5
