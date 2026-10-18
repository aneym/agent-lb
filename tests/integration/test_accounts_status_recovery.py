from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import update

from app.core.usage.refresh_scheduler import reconcile_recoverable_account_statuses
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.usage.repository import UsageRepository
from tests.integration.accounts_api_helpers import _make_account as _make_account
from tests.integration.accounts_api_helpers import openai_credit_override as openai_credit_override

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_accounts_list_recovers_zero_capacity_rate_limited_status(async_client, db_setup):
    expired_reset = int((utcnow() - timedelta(minutes=5)).timestamp())
    account = _make_account("acc_free_recovered_primary", "free-recovered@example.com", plan_type="free")
    account.status = AccountStatus.RATE_LIMITED
    account.reset_at = expired_reset

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)

        await accounts_repo.upsert(account)
        await usage_repo.add_entry(
            "acc_free_recovered_primary",
            24.0,
            window="primary",
            window_minutes=300,
        )
        await usage_repo.add_entry(
            "acc_free_recovered_primary",
            24.0,
            window="secondary",
            window_minutes=10080,
        )

    response = await async_client.get("/api/accounts")
    assert response.status_code == 200
    payload = response.json()
    accounts = {item["accountId"]: item for item in payload["accounts"]}

    account_payload = accounts["acc_free_recovered_primary"]
    assert account_payload["status"] == "active"
    assert account_payload["usage"]["primaryRemainingPercent"] is None
    assert account_payload["usage"]["secondaryRemainingPercent"] == pytest.approx(76.0)
    assert account_payload["windowMinutesPrimary"] is None
    assert account_payload["windowMinutesSecondary"] == 10080


@pytest.mark.asyncio
async def test_accounts_list_preserves_credit_backed_rate_limited_reset_guard(
    async_client, db_setup, openai_credit_override
):
    del openai_credit_override
    future_reset = int((utcnow() + timedelta(hours=2)).timestamp())
    account = _make_account("acc_credit_rate_limited", "credit-rate-limited@example.com")
    account.status = AccountStatus.RATE_LIMITED
    account.reset_at = future_reset
    account.blocked_at = int((utcnow() - timedelta(minutes=10)).timestamp())

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)

        await accounts_repo.upsert(account)
        await usage_repo.add_entry(
            "acc_credit_rate_limited",
            100.0,
            window="secondary",
            reset_at=future_reset,
            window_minutes=10080,
            credits_has=True,
            credits_unlimited=False,
            credits_balance=5.0,
        )

    response = await async_client.get("/api/accounts")
    assert response.status_code == 200
    payload = response.json()
    accounts = {item["accountId"]: item for item in payload["accounts"]}

    assert accounts["acc_credit_rate_limited"]["status"] == "rate_limited"


@pytest.mark.asyncio
async def test_accounts_list_recovers_weekly_only_primary_rate_limited_status(async_client, db_setup):
    future_reset = int((utcnow() + timedelta(days=7)).timestamp())
    account = _make_account("acc_free_weekly_primary_only", "free-weekly-primary@example.com", plan_type="free")
    account.status = AccountStatus.RATE_LIMITED
    account.reset_at = future_reset

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)

        await accounts_repo.upsert(account)
        await usage_repo.add_entry(
            "acc_free_weekly_primary_only",
            24.0,
            window="primary",
            reset_at=future_reset,
            window_minutes=10080,
        )

    response = await async_client.get("/api/accounts")
    assert response.status_code == 200
    payload = response.json()
    accounts = {item["accountId"]: item for item in payload["accounts"]}

    account_payload = accounts["acc_free_weekly_primary_only"]
    assert account_payload["status"] == "active"
    assert account_payload["usage"]["primaryRemainingPercent"] is None
    assert account_payload["usage"]["secondaryRemainingPercent"] == pytest.approx(76.0)
    assert account_payload["windowMinutesPrimary"] is None
    assert account_payload["windowMinutesSecondary"] == 10080


@pytest.mark.asyncio
async def test_accounts_list_keeps_legacy_unknown_primary_rate_limited_until_known_window(async_client, db_setup):
    future_reset = int((utcnow() + timedelta(days=14)).timestamp())
    account = _make_account("acc_free_legacy_unknown_primary", "free-legacy@example.com", plan_type="free")
    account.status = AccountStatus.RATE_LIMITED
    account.reset_at = future_reset

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)

        await accounts_repo.upsert(account)
        await usage_repo.add_entry(
            "acc_free_legacy_unknown_primary",
            100.0,
            window="primary",
            reset_at=future_reset,
            window_minutes=None,
        )
        await usage_repo.add_entry(
            "acc_free_legacy_unknown_primary",
            24.0,
            window="secondary",
            reset_at=future_reset,
            window_minutes=10080,
        )

    response = await async_client.get("/api/accounts")
    assert response.status_code == 200
    payload = response.json()
    accounts = {item["accountId"]: item for item in payload["accounts"]}

    account_payload = accounts["acc_free_legacy_unknown_primary"]
    assert account_payload["status"] == "rate_limited"
    assert account_payload["usage"]["primaryRemainingPercent"] is None
    assert account_payload["usage"]["secondaryRemainingPercent"] == pytest.approx(76.0)
    assert account_payload["windowMinutesPrimary"] is None
    assert account_payload["windowMinutesSecondary"] == 10080


@pytest.mark.asyncio
async def test_accounts_list_keeps_free_rate_limited_until_weekly_quota_available(async_client, db_setup):
    future_reset = int((utcnow() + timedelta(days=14)).timestamp())
    account = _make_account("acc_free_monthly_without_weekly", "free-monthly-no-weekly@example.com", plan_type="free")
    account.status = AccountStatus.RATE_LIMITED
    account.reset_at = future_reset

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)

        await accounts_repo.upsert(account)
        await usage_repo.add_entry(
            "acc_free_monthly_without_weekly",
            100.0,
            window="primary",
            reset_at=future_reset,
            window_minutes=43200,
        )

    response = await async_client.get("/api/accounts")
    assert response.status_code == 200
    payload = response.json()
    accounts = {item["accountId"]: item for item in payload["accounts"]}

    account_payload = accounts["acc_free_monthly_without_weekly"]
    assert account_payload["status"] == "rate_limited"
    assert account_payload["usage"]["primaryRemainingPercent"] is None
    assert account_payload["usage"]["secondaryRemainingPercent"] is None
    assert account_payload["windowMinutesPrimary"] is None
    assert account_payload["windowMinutesSecondary"] is None


@pytest.mark.asyncio
async def test_accounts_list_keeps_rate_limited_without_reset_signal(async_client, db_setup):
    account = _make_account("acc_free_rate_limited_no_reset", "free-no-reset@example.com", plan_type="free")
    account.status = AccountStatus.RATE_LIMITED
    account.reset_at = None
    account.blocked_at = int(utcnow().timestamp())

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)

        await accounts_repo.upsert(account)
        await usage_repo.add_entry(
            "acc_free_rate_limited_no_reset",
            24.0,
            window="primary",
            window_minutes=300,
        )
        await usage_repo.add_entry(
            "acc_free_rate_limited_no_reset",
            24.0,
            window="secondary",
            window_minutes=10080,
        )

    response = await async_client.get("/api/accounts")
    assert response.status_code == 200
    payload = response.json()
    accounts = {item["accountId"]: item for item in payload["accounts"]}

    account_payload = accounts["acc_free_rate_limited_no_reset"]
    assert account_payload["status"] == "rate_limited"
    assert account_payload["usage"]["primaryRemainingPercent"] is None
    assert account_payload["usage"]["secondaryRemainingPercent"] == pytest.approx(76.0)
    assert account_payload["windowMinutesPrimary"] is None
    assert account_payload["windowMinutesSecondary"] == 10080


@pytest.mark.asyncio
async def test_accounts_list_keeps_quota_exceeded_reset_when_ignoring_monthly_primary(async_client, db_setup):
    future_reset = int((utcnow() + timedelta(days=14)).timestamp())
    account = _make_account("acc_free_quota_exceeded_monthly", "free-quota-monthly@example.com", plan_type="free")
    account.status = AccountStatus.QUOTA_EXCEEDED
    account.reset_at = future_reset

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)

        await accounts_repo.upsert(account)
        await usage_repo.add_entry(
            "acc_free_quota_exceeded_monthly",
            100.0,
            window="primary",
            reset_at=future_reset,
            window_minutes=43200,
        )
        await usage_repo.add_entry(
            "acc_free_quota_exceeded_monthly",
            24.0,
            window="secondary",
            reset_at=future_reset,
            window_minutes=10080,
        )

    response = await async_client.get("/api/accounts")
    assert response.status_code == 200
    payload = response.json()
    accounts = {item["accountId"]: item for item in payload["accounts"]}

    account_payload = accounts["acc_free_quota_exceeded_monthly"]
    assert account_payload["status"] == "quota_exceeded"
    assert account_payload["usage"]["primaryRemainingPercent"] is None
    assert account_payload["usage"]["secondaryRemainingPercent"] == pytest.approx(76.0)
    assert account_payload["windowMinutesPrimary"] is None
    assert account_payload["windowMinutesSecondary"] == 10080


@pytest.mark.asyncio
async def test_accounts_list_recovers_quota_exceeded_status_from_secondary_usage(async_client, db_setup):
    expired_reset = int((utcnow() - timedelta(minutes=5)).timestamp())
    blocked_at = int((utcnow() - timedelta(hours=2)).timestamp())
    account = _make_account("acc_quota_recovered_secondary", "quota-recovered@example.com")
    account.status = AccountStatus.QUOTA_EXCEEDED
    account.reset_at = expired_reset
    account.blocked_at = blocked_at

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)

        await accounts_repo.upsert(account)
        await usage_repo.add_entry(
            "acc_quota_recovered_secondary",
            42.0,
            window="secondary",
            reset_at=int((utcnow() + timedelta(days=1)).timestamp()),
            window_minutes=10080,
        )

    response = await async_client.get("/api/accounts")
    assert response.status_code == 200
    payload = response.json()
    accounts = {item["accountId"]: item for item in payload["accounts"]}

    account_payload = accounts["acc_quota_recovered_secondary"]
    assert account_payload["status"] == "active"
    assert account_payload["usage"]["secondaryRemainingPercent"] == pytest.approx(58.0)


@pytest.mark.asyncio
async def test_accounts_list_stale_rate_limited_status_recovers_after_background_reconcile(
    async_client,
    db_setup,
    monkeypatch: pytest.MonkeyPatch,
):
    now = 1_700_000_000.0
    past_reset = int(now - 300)
    blocked_at = int(now - 7200)
    monkeypatch.setattr("app.modules.proxy.load_balancer.time.time", lambda: now)
    monkeypatch.setattr("app.core.usage.quota.time.time", lambda: now)
    monkeypatch.setattr(
        "app.modules.proxy.load_balancer.utcnow",
        lambda: datetime.fromtimestamp(now, tz=timezone.utc).replace(tzinfo=None),
    )

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)

        await accounts_repo.upsert(_make_account("acc_stuck_status", "stuck@example.com"))
        await session.execute(
            update(Account)
            .where(Account.id == "acc_stuck_status")
            .values(
                status=AccountStatus.RATE_LIMITED,
                reset_at=past_reset,
                blocked_at=blocked_at,
                deactivation_reason=None,
            )
        )
        await session.commit()

        await usage_repo.add_entry(
            "acc_stuck_status",
            10.0,
            window="primary",
            reset_at=past_reset,
            window_minutes=300,
            recorded_at=datetime.fromtimestamp(now - 10, tz=timezone.utc).replace(tzinfo=None),
        )
        await usage_repo.add_entry(
            "acc_stuck_status",
            20.0,
            window="secondary",
            reset_at=int(now + 7200),
            window_minutes=10080,
            recorded_at=datetime.fromtimestamp(now - 10, tz=timezone.utc).replace(tzinfo=None),
        )

    stale = await async_client.get("/api/accounts")
    assert stale.status_code == 200
    stale_account = next(item for item in stale.json()["accounts"] if item["accountId"] == "acc_stuck_status")
    assert stale_account["status"] == "rate_limited"
    assert stale_account["usage"]["primaryRemainingPercent"] == pytest.approx(90.0)

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)
        account = await accounts_repo.get_by_id("acc_stuck_status")
        assert account is not None
        recovered = await reconcile_recoverable_account_statuses(
            accounts_repo=accounts_repo,
            usage_repo=usage_repo,
            accounts=[account],
        )
        assert recovered == 1

    reconciled = await async_client.get("/api/accounts")
    assert reconciled.status_code == 200
    reconciled_account = next(item for item in reconciled.json()["accounts"] if item["accountId"] == "acc_stuck_status")
    assert reconciled_account["status"] == "active"
    assert reconciled_account["usage"]["primaryRemainingPercent"] == pytest.approx(90.0)
