from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.utils.time import utcnow
from app.db.models import AccountStatus
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.usage.repository import UsageRepository
from tests.integration.accounts_api_helpers import _iso_utc as _iso_utc
from tests.integration.accounts_api_helpers import _make_account as _make_account

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_accounts_list_includes_per_account_reset_times(async_client, db_setup):
    primary_a = 1735689600
    primary_b = 1735693200
    secondary_a = 1736294400
    secondary_b = 1736380800

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)

        await accounts_repo.upsert(_make_account("acc_reset_a", "a@example.com"))
        await accounts_repo.upsert(_make_account("acc_reset_b", "b@example.com"))

        await usage_repo.add_entry(
            "acc_reset_a",
            10.0,
            window="primary",
            reset_at=primary_a,
            window_minutes=300,
        )
        await usage_repo.add_entry(
            "acc_reset_b",
            20.0,
            window="primary",
            reset_at=primary_b,
            window_minutes=300,
        )
        await usage_repo.add_entry(
            "acc_reset_a",
            30.0,
            window="secondary",
            reset_at=secondary_a,
            window_minutes=10080,
        )
        await usage_repo.add_entry(
            "acc_reset_b",
            40.0,
            window="secondary",
            reset_at=secondary_b,
            window_minutes=10080,
        )

    response = await async_client.get("/api/accounts")
    assert response.status_code == 200
    payload = response.json()
    accounts = {item["accountId"]: item for item in payload["accounts"]}

    assert accounts["acc_reset_a"]["resetAtPrimary"] == _iso_utc(primary_a)
    assert accounts["acc_reset_b"]["resetAtPrimary"] == _iso_utc(primary_b)
    assert accounts["acc_reset_a"]["resetAtSecondary"] == _iso_utc(secondary_a)
    assert accounts["acc_reset_b"]["resetAtSecondary"] == _iso_utc(secondary_b)
    assert accounts["acc_reset_a"]["windowMinutesPrimary"] == 300
    assert accounts["acc_reset_b"]["windowMinutesPrimary"] == 300
    assert accounts["acc_reset_a"]["windowMinutesSecondary"] == 10080
    assert accounts["acc_reset_b"]["windowMinutesSecondary"] == 10080


@pytest.mark.asyncio
async def test_accounts_list_exposes_anthropic_oauth_usage_windows(async_client, db_setup):
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)

        account = _make_account("acc_claude_windows", "claude-windows@example.com", plan_type="max")
        account.provider = "anthropic"
        await accounts_repo.upsert(account)
        await usage_repo.add_entry(
            "acc_claude_windows",
            20.0,
            provider="anthropic",
            window="primary",
            reset_at=1781034600,
            window_minutes=300,
        )
        await usage_repo.add_entry(
            "acc_claude_windows",
            96.0,
            provider="anthropic",
            window="secondary",
            reset_at=1781470800,
            window_minutes=10080,
        )

    response = await async_client.get("/api/accounts")
    assert response.status_code == 200
    payload = response.json()
    accounts = {item["accountId"]: item for item in payload["accounts"]}

    account_payload = accounts["acc_claude_windows"]
    assert account_payload["provider"] == "anthropic"
    assert account_payload["usage"]["primaryRemainingPercent"] == pytest.approx(80.0)
    assert account_payload["usage"]["secondaryRemainingPercent"] == pytest.approx(4.0)
    assert account_payload["resetAtPrimary"] == _iso_utc(1781034600)
    assert account_payload["resetAtSecondary"] == _iso_utc(1781470800)
    assert account_payload["windowMinutesPrimary"] == 300
    assert account_payload["windowMinutesSecondary"] == 10080


@pytest.mark.asyncio
async def test_accounts_list_maps_weekly_only_primary_to_secondary(async_client, db_setup):
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)

        await accounts_repo.upsert(_make_account("acc_free_like", "free@example.com", plan_type="free"))
        await usage_repo.add_entry(
            "acc_free_like",
            24.0,
            window="primary",
            window_minutes=10080,
        )

    response = await async_client.get("/api/accounts")
    assert response.status_code == 200
    payload = response.json()
    accounts = {item["accountId"]: item for item in payload["accounts"]}

    account = accounts["acc_free_like"]
    assert account["usage"]["primaryRemainingPercent"] is None
    assert account["usage"]["secondaryRemainingPercent"] == pytest.approx(76.0)
    assert account["windowMinutesPrimary"] is None
    assert account["windowMinutesSecondary"] == 10080


@pytest.mark.asyncio
async def test_accounts_list_exposes_monthly_only_free_quota(async_client, db_setup):
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)

        await accounts_repo.upsert(_make_account("acc_free_monthly", "free-monthly@example.com", plan_type="free"))
        await usage_repo.add_entry(
            "acc_free_monthly",
            24.0,
            window="monthly",
            window_minutes=43200,
        )

    response = await async_client.get("/api/accounts")
    assert response.status_code == 200
    payload = response.json()
    accounts = {item["accountId"]: item for item in payload["accounts"]}

    account = accounts["acc_free_monthly"]
    assert account["usage"]["primaryRemainingPercent"] is None
    assert account["usage"]["secondaryRemainingPercent"] is None
    assert account["usage"]["monthlyRemainingPercent"] == pytest.approx(76.0)
    assert account["windowMinutesPrimary"] is None
    assert account["windowMinutesSecondary"] is None
    assert account["windowMinutesMonthly"] == 43200


@pytest.mark.asyncio
async def test_accounts_list_ignores_stale_monthly_quota_after_upgrade(async_client, db_setup):
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)

        await accounts_repo.upsert(
            _make_account("acc_upgraded_monthly", "upgraded-monthly@example.com", plan_type="plus")
        )
        await usage_repo.add_entry(
            "acc_upgraded_monthly",
            100.0,
            window="monthly",
            window_minutes=43200,
        )
        await usage_repo.add_entry(
            "acc_upgraded_monthly",
            10.0,
            window="primary",
            window_minutes=300,
        )
        await usage_repo.add_entry(
            "acc_upgraded_monthly",
            20.0,
            window="secondary",
            window_minutes=10080,
        )

    response = await async_client.get("/api/accounts")
    assert response.status_code == 200
    payload = response.json()
    accounts = {item["accountId"]: item for item in payload["accounts"]}

    account = accounts["acc_upgraded_monthly"]
    assert account["status"] == AccountStatus.ACTIVE.value
    assert account["usage"]["primaryRemainingPercent"] == pytest.approx(90.0)
    assert account["usage"]["secondaryRemainingPercent"] == pytest.approx(80.0)
    assert account["usage"]["monthlyRemainingPercent"] is None
    assert account["windowMinutesPrimary"] == 300
    assert account["windowMinutesSecondary"] == 10080
    assert account["windowMinutesMonthly"] is None


@pytest.mark.asyncio
async def test_accounts_list_ignores_hidden_zero_capacity_primary_for_status(async_client, db_setup):
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)

        await accounts_repo.upsert(_make_account("acc_free_dirty_primary", "free-dirty@example.com", plan_type="free"))
        await usage_repo.add_entry(
            "acc_free_dirty_primary",
            100.0,
            window="primary",
            window_minutes=300,
        )
        await usage_repo.add_entry(
            "acc_free_dirty_primary",
            24.0,
            window="secondary",
            window_minutes=10080,
        )

    response = await async_client.get("/api/accounts")
    assert response.status_code == 200
    payload = response.json()
    accounts = {item["accountId"]: item for item in payload["accounts"]}

    account = accounts["acc_free_dirty_primary"]
    assert account["status"] == "active"
    assert account["usage"]["primaryRemainingPercent"] is None
    assert account["usage"]["secondaryRemainingPercent"] == pytest.approx(76.0)
    assert account["windowMinutesPrimary"] is None
    assert account["windowMinutesSecondary"] == 10080


@pytest.mark.asyncio
async def test_accounts_list_ignores_hidden_zero_capacity_primary_without_weekly_for_active_account(
    async_client, db_setup
):
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)

        await accounts_repo.upsert(
            _make_account("acc_free_active_primary_only", "free-primary-only@example.com", plan_type="free")
        )
        await usage_repo.add_entry(
            "acc_free_active_primary_only",
            100.0,
            window="primary",
            window_minutes=43200,
        )

    response = await async_client.get("/api/accounts")
    assert response.status_code == 200
    payload = response.json()
    accounts = {item["accountId"]: item for item in payload["accounts"]}

    account = accounts["acc_free_active_primary_only"]
    assert account["status"] == "active"
    assert account["usage"]["primaryRemainingPercent"] is None
    assert account["usage"]["secondaryRemainingPercent"] is None
    assert account["windowMinutesPrimary"] is None
    assert account["windowMinutesSecondary"] is None


@pytest.mark.asyncio
async def test_accounts_list_ignores_zero_capacity_monthly_primary_status(async_client, db_setup):
    future_reset = int((utcnow() + timedelta(days=14)).timestamp())
    account = _make_account("acc_free_monthly_primary", "free-monthly@example.com", plan_type="free")
    account.status = AccountStatus.RATE_LIMITED
    account.reset_at = future_reset

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)

        await accounts_repo.upsert(account)
        await usage_repo.add_entry(
            "acc_free_monthly_primary",
            100.0,
            window="primary",
            reset_at=future_reset,
            window_minutes=43200,
        )
        await usage_repo.add_entry(
            "acc_free_monthly_primary",
            24.0,
            window="secondary",
            reset_at=future_reset,
            window_minutes=10080,
        )

    response = await async_client.get("/api/accounts")
    assert response.status_code == 200
    payload = response.json()
    accounts = {item["accountId"]: item for item in payload["accounts"]}

    account_payload = accounts["acc_free_monthly_primary"]
    assert account_payload["status"] == "active"
    assert account_payload["usage"]["primaryRemainingPercent"] is None
    assert account_payload["usage"]["secondaryRemainingPercent"] == pytest.approx(76.0)
    assert account_payload["windowMinutesPrimary"] is None
    assert account_payload["windowMinutesSecondary"] == 10080


@pytest.mark.asyncio
async def test_accounts_list_prefers_newer_weekly_primary_over_stale_secondary(async_client, db_setup):
    now = utcnow()
    stale_reset = 1735689600
    weekly_reset = 1735862400

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)

        await accounts_repo.upsert(_make_account("acc_weekly_stale", "weekly-stale@example.com", plan_type="free"))
        await usage_repo.add_entry(
            "acc_weekly_stale",
            15.0,
            window="secondary",
            reset_at=stale_reset,
            window_minutes=10080,
            recorded_at=now - timedelta(days=2),
        )
        await usage_repo.add_entry(
            "acc_weekly_stale",
            80.0,
            window="primary",
            reset_at=weekly_reset,
            window_minutes=10080,
            recorded_at=now,
        )

    response = await async_client.get("/api/accounts")
    assert response.status_code == 200
    payload = response.json()
    accounts = {item["accountId"]: item for item in payload["accounts"]}

    account = accounts["acc_weekly_stale"]
    assert account["usage"]["primaryRemainingPercent"] is None
    assert account["usage"]["secondaryRemainingPercent"] == pytest.approx(20.0)
    assert account["windowMinutesPrimary"] is None
    assert account["windowMinutesSecondary"] == 10080
    assert account["resetAtSecondary"] == _iso_utc(weekly_reset)
