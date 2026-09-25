from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core.auth.dependencies import validate_dashboard_session
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, AdditionalUsageHistory, UsageHistory
from app.db.session import SessionLocal
from app.modules.pools.cli_seats import SeatAccount, SeatAccountsResponse

pytestmark = pytest.mark.integration

_POOL_IDS = ["anthropic-fable", "anthropic-general", "openai-codex", "kimi", "glm"]


def _account(account_id: str, provider: str, encryptor: TokenEncryptor) -> Account:
    return Account(
        id=account_id,
        provider=provider,
        chatgpt_account_id=account_id,
        email=f"{account_id}@example.com",
        plan_type="max",
        access_token_encrypted=encryptor.encrypt(f"access-{account_id}"),
        refresh_token_encrypted=encryptor.encrypt(f"refresh-{account_id}"),
        id_token_encrypted=encryptor.encrypt(f"id-{account_id}"),
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
    )


def _weekly_usage(account_id: str, provider: str, used_percent: float) -> UsageHistory:
    return UsageHistory(
        account_id=account_id,
        provider=provider,
        window="secondary",
        used_percent=used_percent,
        window_minutes=10080,
        recorded_at=utcnow(),
    )


def _five_hour_usage(account_id: str, used_percent: float, reset_at: int) -> UsageHistory:
    return UsageHistory(
        account_id=account_id,
        provider="anthropic",
        window="primary",
        used_percent=used_percent,
        reset_at=reset_at,
        window_minutes=300,
        recorded_at=utcnow(),
    )


def _fable_scoped_weekly(account_id: str, used_percent: float, recorded_at) -> AdditionalUsageHistory:
    return AdditionalUsageHistory(
        account_id=account_id,
        quota_key="anthropic_fable_scoped_weekly",
        limit_name="anthropic_fable_scoped_weekly",
        metered_feature="anthropic_fable_scoped_weekly",
        window="primary",
        used_percent=used_percent,
        window_minutes=10080,
        recorded_at=recorded_at,
    )


@pytest.mark.asyncio
async def test_pools_router_declares_dashboard_auth_dependency(app_instance) -> None:
    pools_routes = [route for route in app_instance.routes if getattr(route, "path", "").startswith("/api/pools")]

    assert pools_routes
    for route in pools_routes:
        dependencies = [dependency.call for dependency in route.dependant.dependencies]
        assert validate_dashboard_session in dependencies


@pytest.mark.asyncio
async def test_pools_reports_every_contract_pool_with_no_accounts(async_client, db_setup) -> None:
    del db_setup

    response = await async_client.get("/api/pools")

    assert response.status_code == 200
    body = response.json()
    assert body["generatedAt"].endswith("Z")
    assert [pool["id"] for pool in body["pools"]] == _POOL_IDS
    for pool in body["pools"]:
        assert pool["accounts"] == 0
        assert pool["eligibleAccounts"] == 0
        assert pool["headroomPercent"] is None
        assert pool["status"] == "exhausted"


@pytest.mark.asyncio
async def test_pools_aggregates_live_windows_and_marks_the_fable_source(async_client, db_setup, monkeypatch) -> None:
    del db_setup
    encryptor = TokenEncryptor()
    now = utcnow()
    early_reset = int((now + timedelta(hours=2)).replace(tzinfo=timezone.utc).timestamp())
    late_reset = int((now + timedelta(hours=4)).replace(tzinfo=timezone.utc).timestamp())

    async with SessionLocal() as session:
        # Fable-scoped marker fresh and nearly spent, weekly window healthy:
        # the two Anthropic pools must disagree.
        session.add(_account("acc-fable-hot", "anthropic", encryptor))
        session.add(_weekly_usage("acc-fable-hot", "anthropic", 30.0))
        session.add(_five_hour_usage("acc-fable-hot", 45.0, late_reset))
        session.add(_fable_scoped_weekly("acc-fable-hot", 87.0, now))

        session.add(_account("acc-fable-cool", "anthropic", encryptor))
        session.add(_weekly_usage("acc-fable-cool", "anthropic", 40.0))
        session.add(_five_hour_usage("acc-fable-cool", 20.0, early_reset))
        session.add(_fable_scoped_weekly("acc-fable-cool", 6.0, now))

        paused = _account("acc-paused", "anthropic", encryptor)
        paused.status = AccountStatus.PAUSED
        session.add(paused)
        session.add(_five_hour_usage("acc-paused", 0.0, early_reset - 600))

        limited = _account("acc-limited", "anthropic", encryptor)
        limited.status = AccountStatus.RATE_LIMITED
        limited.reset_at = int((now + timedelta(hours=1)).replace(tzinfo=timezone.utc).timestamp())
        session.add(limited)
        session.add(_five_hour_usage("acc-limited", 0.0, early_reset - 300))

        session.add(_account("acc-codex", "openai", encryptor))
        session.add(_weekly_usage("acc-codex", "openai", 55.0))

        session.add(_account("acc-glm", "glm", encryptor))

        await session.commit()

    monkeypatch.setattr(
        "app.modules.pools.service.read_seat_accounts",
        lambda: SeatAccountsResponse(source="seat_state", accounts=[SeatAccount(id="cursor-1", vendor="cursor")]),
    )
    response = await async_client.get("/api/pools")

    assert response.status_code == 200
    pools = {pool["id"]: pool for pool in response.json()["pools"]}

    fable = pools["anthropic-fable"]
    assert fable["provider"] == "anthropic"
    assert fable["kind"] == "fable_scoped"
    assert fable["source"] == "scoped_marker"
    assert fable["accounts"] == 4
    assert fable["eligibleAccounts"] == 2
    assert fable["headroomPercent"] == pytest.approx(80.0)
    assert fable["aggregateRemainingPercent"] == pytest.approx(31.0)
    assert fable["status"] == "ok"

    general = pools["anthropic-general"]
    assert general["kind"] == "weekly"
    assert general["headroomPercent"] == pytest.approx(60.0)
    assert general["source"] is None

    for pool in (fable, general):
        assert pool["fiveHourRemainingPercent"] == pytest.approx(80.0)
        assert pool["fiveHourResetAt"] == datetime.fromtimestamp(early_reset, timezone.utc).isoformat().replace(
            "+00:00", "Z"
        )
        assert pool["windowLabel"] == "week"
    assert pools["openai-codex"]["fiveHourRemainingPercent"] is None
    assert pools["openai-codex"]["fiveHourResetAt"] is None
    assert pools["openai-codex"]["windowLabel"] == "week"
    assert pools["openai-codex"]["headroomPercent"] == pytest.approx(45.0)
    assert pools["glm"]["headroomPercent"] == pytest.approx(100.0)
    assert pools["kimi"]["accounts"] == 0
    assert pools["cursor"]["kind"] == "cli_seat"
    assert pools["cursor"]["windowLabel"] == "month"
    assert pools["cursor"]["fiveHourRemainingPercent"] is None
    assert pools["cursor"]["fiveHourResetAt"] is None


@pytest.mark.asyncio
async def test_pools_falls_back_to_the_weekly_heuristic_when_markers_are_stale(async_client, db_setup) -> None:
    del db_setup
    encryptor = TokenEncryptor()
    now = utcnow()

    async with SessionLocal() as session:
        session.add(_account("acc-stale", "anthropic", encryptor))
        session.add(_weekly_usage("acc-stale", "anthropic", 12.0))
        session.add(_fable_scoped_weekly("acc-stale", 100.0, now - timedelta(hours=7)))
        await session.commit()

    response = await async_client.get("/api/pools")

    assert response.status_code == 200
    fable = next(pool for pool in response.json()["pools"] if pool["id"] == "anthropic-fable")
    assert fable["source"] == "weekly_heuristic"
    assert fable["headroomPercent"] == pytest.approx(88.0)


@pytest.mark.asyncio
async def test_accounts_exposes_the_fable_scoped_window(async_client, db_setup) -> None:
    del db_setup
    encryptor = TokenEncryptor()
    now = utcnow()

    async with SessionLocal() as session:
        session.add(_account("acc-marked", "anthropic", encryptor))
        session.add(_fable_scoped_weekly("acc-marked", 87.0, now))
        session.add(_account("acc-unmarked", "anthropic", encryptor))
        await session.commit()

    response = await async_client.get("/api/accounts")

    assert response.status_code == 200
    accounts = {account["accountId"]: account for account in response.json()["accounts"]}
    marked = accounts["acc-marked"]["fableScopedWeekly"]
    assert marked["usedPercent"] == pytest.approx(87.0)
    assert marked["fresh"] is True
    assert marked["recordedAt"].endswith("Z")
    assert accounts["acc-unmarked"]["fableScopedWeekly"] is None
