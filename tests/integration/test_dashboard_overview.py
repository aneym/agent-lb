from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.core.utils.time import naive_utc_to_epoch, utcnow
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.request_logs.repository import RequestLogsRepository
from app.modules.usage.repository import UsageRepository
from tests.integration.dashboard_overview_support import (
    _make_account as _make_account,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_dashboard_overview_combines_data(async_client, db_setup):
    now = utcnow().replace(microsecond=0)
    primary_time = now - timedelta(minutes=5)
    secondary_time = now - timedelta(minutes=2)

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)
        logs_repo = RequestLogsRepository(session)

        await accounts_repo.upsert(_make_account("acc_dash", "dash@example.com"))
        await usage_repo.add_entry(
            "acc_dash",
            20.0,
            window="primary",
            recorded_at=primary_time,
        )
        await usage_repo.add_entry(
            "acc_dash",
            40.0,
            window="secondary",
            recorded_at=secondary_time,
        )
        await logs_repo.add_log(
            account_id="acc_dash",
            request_id="req_dash_1",
            model="gpt-5.1",
            input_tokens=100,
            output_tokens=50,
            latency_ms=50,
            status="success",
            error_code=None,
            requested_at=now - timedelta(minutes=1),
        )

    response = await async_client.get("/api/dashboard/overview")
    assert response.status_code == 200
    payload = response.json()

    assert payload["accounts"][0]["accountId"] == "acc_dash"
    assert payload["accounts"][0]["capacityCreditsSecondary"] == pytest.approx(7560.0)
    assert payload["accounts"][0]["remainingCreditsSecondary"] == pytest.approx(4536.0)
    assert payload["timeframe"] == {
        "key": "7d",
        "windowMinutes": 10080,
        "bucketSeconds": 21600,
        "bucketCount": 28,
    }
    assert payload["summary"]["primaryWindow"]["capacityCredits"] == pytest.approx(225.0)
    assert payload["summary"]["cost"]["totalUsd"] == pytest.approx(0.000625)
    assert payload["summary"]["metrics"]["requests"] == 1
    assert payload["summary"]["metrics"]["tokens"] == 150
    assert payload["summary"]["metrics"]["cachedInputTokens"] == 0
    assert payload["summary"]["metrics"]["errorRate"] == pytest.approx(0.0)
    assert payload["summary"]["metrics"]["errorCount"] == 0
    assert payload["windows"]["primary"]["windowKey"] == "primary"
    assert payload["windows"]["secondary"]["windowKey"] == "secondary"
    assert "requestLogs" not in payload
    assert payload["lastSyncAt"] == secondary_time.isoformat() + "Z"

    assert "trends" in payload
    trends = payload["trends"]
    assert len(trends["requests"]) == 28
    assert len(trends["tokens"]) == 28
    assert len(trends["cost"]) == 28
    assert len(trends["errorRate"]) == 28

    request_values = [p["v"] for p in trends["requests"]]
    assert any(v > 0 for v in request_values)


@pytest.mark.asyncio
async def test_dashboard_overview_hides_canceled_subscription_accounts(async_client, db_setup):
    now = utcnow().replace(microsecond=0)
    active = _make_account("acc_dash_active_sub", "dash-active@example.com")
    canceled = _make_account("acc_dash_canceled_sub", "dash-canceled@example.com")
    canceled.subscription_status = "canceled"

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)
        logs_repo = RequestLogsRepository(session)

        await accounts_repo.upsert(active)
        await accounts_repo.upsert(canceled)
        await usage_repo.add_entry(
            active.id,
            20.0,
            window="primary",
            recorded_at=now - timedelta(minutes=2),
        )
        await usage_repo.add_entry(
            canceled.id,
            80.0,
            window="primary",
            recorded_at=now - timedelta(minutes=1),
        )
        await logs_repo.add_log(
            account_id=active.id,
            request_id="req_dash_active_sub",
            model="gpt-5.1",
            input_tokens=20,
            output_tokens=10,
            latency_ms=40,
            status="success",
            error_code=None,
            requested_at=now - timedelta(minutes=1),
        )
        await logs_repo.add_log(
            account_id=canceled.id,
            request_id="req_dash_canceled_sub",
            model="gpt-5.1",
            input_tokens=200,
            output_tokens=100,
            latency_ms=40,
            status="error",
            error_code="subscription_canceled",
            requested_at=now - timedelta(minutes=1),
        )

    response = await async_client.get("/api/dashboard/overview")
    assert response.status_code == 200
    payload = response.json()

    assert [account["accountId"] for account in payload["accounts"]] == [active.id]
    assert payload["summary"]["primaryWindow"]["capacityCredits"] == pytest.approx(225.0)
    assert payload["summary"]["cost"]["totalUsd"] == pytest.approx(0.000125)
    assert payload["summary"]["metrics"]["requests"] == 1
    assert payload["summary"]["metrics"]["tokens"] == 30
    assert payload["summary"]["metrics"]["errorCount"] == 0
    assert payload["summary"]["metrics"]["topError"] is None
    assert [account["accountId"] for account in payload["windows"]["primary"]["accounts"]] == [active.id]
    assert sum(point["v"] for point in payload["trends"]["requests"]) == 1


@pytest.mark.asyncio
async def test_dashboard_overview_metrics_keep_soft_deleted_request_logs(async_client, db_setup):
    now = utcnow().replace(microsecond=0)

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_account("acc_dash_deleted", "dash-deleted@example.com"))
        await logs_repo.add_log(
            account_id="acc_dash_deleted",
            request_id="req_dash_deleted_1",
            model="gpt-5.1",
            input_tokens=40,
            output_tokens=10,
            latency_ms=40,
            status="success",
            error_code=None,
            requested_at=now - timedelta(minutes=2),
        )

    delete_response = await async_client.delete("/api/accounts/acc_dash_deleted")
    assert delete_response.status_code == 200

    overview = await async_client.get("/api/dashboard/overview")
    assert overview.status_code == 200
    payload = overview.json()

    assert payload["accounts"] == []
    assert payload["summary"]["metrics"]["requests"] == 1
    assert payload["summary"]["metrics"]["tokens"] == 50
    request_values = [point["v"] for point in payload["trends"]["requests"]]
    assert any(value > 0 for value in request_values)


@pytest.mark.asyncio
async def test_dashboard_overview_maps_weekly_only_primary_to_secondary(async_client, db_setup):
    now = utcnow().replace(microsecond=0)

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)

        await accounts_repo.upsert(_make_account("acc_plus", "plus@example.com", plan_type="plus"))
        await accounts_repo.upsert(_make_account("acc_free", "free@example.com", plan_type="free"))

        await usage_repo.add_entry(
            "acc_plus",
            20.0,
            window="primary",
            window_minutes=300,
            recorded_at=now - timedelta(minutes=2),
        )
        await usage_repo.add_entry(
            "acc_free",
            20.0,
            window="primary",
            window_minutes=10080,
            recorded_at=now - timedelta(minutes=1),
        )
        await usage_repo.add_entry(
            "acc_plus",
            40.0,
            window="secondary",
            window_minutes=10080,
            recorded_at=now - timedelta(minutes=1),
        )

    response = await async_client.get("/api/dashboard/overview")
    assert response.status_code == 200
    payload = response.json()

    accounts = {item["accountId"]: item for item in payload["accounts"]}

    assert payload["summary"]["primaryWindow"]["windowMinutes"] == 300
    assert payload["windows"]["primary"]["windowMinutes"] == 300
    assert payload["summary"]["secondaryWindow"]["windowMinutes"] == 10080
    assert accounts["acc_free"]["windowMinutesPrimary"] is None
    assert accounts["acc_free"]["windowMinutesSecondary"] == 10080
    assert accounts["acc_free"]["usage"]["secondaryRemainingPercent"] == pytest.approx(80.0)


@pytest.mark.asyncio
async def test_dashboard_overview_exposes_monthly_only_free_account(async_client, db_setup):
    now = utcnow().replace(microsecond=0)

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)

        await accounts_repo.upsert(_make_account("acc_free_monthly", "free-monthly@example.com", plan_type="free"))
        await usage_repo.add_entry(
            "acc_free_monthly",
            20.0,
            window="monthly",
            window_minutes=43200,
            recorded_at=now - timedelta(minutes=1),
        )

    response = await async_client.get("/api/dashboard/overview")
    assert response.status_code == 200
    payload = response.json()

    accounts = {item["accountId"]: item for item in payload["accounts"]}
    account = accounts["acc_free_monthly"]
    assert payload["lastSyncAt"] == (now - timedelta(minutes=1)).isoformat() + "Z"
    assert account["usage"]["monthlyRemainingPercent"] == pytest.approx(80.0)
    assert account["windowMinutesPrimary"] is None
    assert account["windowMinutesSecondary"] is None
    assert account["windowMinutesMonthly"] == 43200


@pytest.mark.asyncio
async def test_dashboard_overview_derives_quota_status_from_current_weekly_usage(async_client, db_setup):
    now = utcnow().replace(microsecond=0)

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)

        await accounts_repo.upsert(_make_account("acc_weekly_full", "weekly-full@example.com"))
        await usage_repo.add_entry(
            "acc_weekly_full",
            5.0,
            window="primary",
            window_minutes=300,
            recorded_at=now - timedelta(minutes=2),
        )
        await usage_repo.add_entry(
            "acc_weekly_full",
            100.0,
            window="secondary",
            window_minutes=10080,
            reset_at=int(naive_utc_to_epoch(now + timedelta(days=2))),
            recorded_at=now - timedelta(minutes=1),
        )

    response = await async_client.get("/api/dashboard/overview")
    assert response.status_code == 200
    payload = response.json()

    accounts = {item["accountId"]: item for item in payload["accounts"]}
    account = accounts["acc_weekly_full"]
    assert account["status"] == "quota_exceeded"
    assert account["usage"]["primaryRemainingPercent"] == pytest.approx(95.0)
    assert account["usage"]["secondaryRemainingPercent"] == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_dashboard_overview_counts_prolite_capacity(async_client, db_setup):
    now = utcnow().replace(microsecond=0)

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)

        await accounts_repo.upsert(_make_account("acc_prolite", "prolite@example.com", plan_type="prolite"))
        await usage_repo.add_entry(
            "acc_prolite",
            0.0,
            window="primary",
            window_minutes=300,
            recorded_at=now - timedelta(minutes=2),
        )
        await usage_repo.add_entry(
            "acc_prolite",
            0.0,
            window="secondary",
            window_minutes=10080,
            recorded_at=now - timedelta(minutes=1),
        )

    response = await async_client.get("/api/dashboard/overview")
    assert response.status_code == 200
    payload = response.json()
    account = payload["accounts"][0]

    assert account["planType"] == "prolite"
    assert account["capacityCreditsPrimary"] == pytest.approx(1125.0)
    assert account["remainingCreditsPrimary"] == pytest.approx(1125.0)
    assert account["capacityCreditsSecondary"] == pytest.approx(37800.0)
    assert account["remainingCreditsSecondary"] == pytest.approx(37800.0)
    assert payload["summary"]["primaryWindow"]["capacityCredits"] == pytest.approx(1125.0)
    assert payload["summary"]["primaryWindow"]["remainingCredits"] == pytest.approx(1125.0)
    assert payload["summary"]["secondaryWindow"]["capacityCredits"] == pytest.approx(37800.0)
    assert payload["summary"]["secondaryWindow"]["remainingCredits"] == pytest.approx(37800.0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("timeframe", "expected_requests", "expected_bucket_count"),
    [
        ("1d", 1, 24),
        ("30d", 2, 30),
    ],
)
async def test_dashboard_overview_respects_selected_timeframe(
    async_client,
    db_setup,
    timeframe: str,
    expected_requests: int,
    expected_bucket_count: int,
):
    now = utcnow().replace(microsecond=0)

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)
        logs_repo = RequestLogsRepository(session)

        await accounts_repo.upsert(_make_account("acc_timeframe", "timeframe@example.com"))
        await usage_repo.add_entry(
            "acc_timeframe",
            20.0,
            window="primary",
            recorded_at=now - timedelta(minutes=5),
        )
        await usage_repo.add_entry(
            "acc_timeframe",
            40.0,
            window="secondary",
            recorded_at=now - timedelta(minutes=2),
        )
        await logs_repo.add_log(
            account_id="acc_timeframe",
            request_id="req_recent",
            model="gpt-5.1",
            input_tokens=100,
            output_tokens=50,
            latency_ms=50,
            status="success",
            error_code=None,
            requested_at=now - timedelta(hours=3),
        )
        await logs_repo.add_log(
            account_id="acc_timeframe",
            request_id="req_old",
            model="gpt-5.1",
            input_tokens=200,
            output_tokens=100,
            latency_ms=50,
            status="error",
            error_code="rate_limit_exceeded",
            requested_at=now - timedelta(days=2),
        )

    response = await async_client.get(f"/api/dashboard/overview?timeframe={timeframe}")
    assert response.status_code == 200
    payload = response.json()

    assert payload["timeframe"]["key"] == timeframe
    assert payload["timeframe"]["bucketCount"] == expected_bucket_count
    assert len(payload["trends"]["requests"]) == expected_bucket_count
    assert payload["summary"]["metrics"]["requests"] == expected_requests
    if timeframe == "1d":
        assert payload["summary"]["metrics"]["errorCount"] == 0
        assert payload["summary"]["metrics"]["topError"] is None
    else:
        assert payload["summary"]["metrics"]["errorCount"] == 1
        assert payload["summary"]["metrics"]["topError"] == "rate_limit_exceeded"


@pytest.mark.asyncio
async def test_dashboard_overview_invalid_timeframe_returns_validation_error(async_client):
    response = await async_client.get("/api/dashboard/overview?timeframe=90d")
    assert response.status_code == 422

    payload = response.json()
    assert payload["error"]["code"] == "validation_error"


@pytest.mark.asyncio
async def test_dashboard_overview_summary_uses_exact_timeframe_even_when_trends_skip_partial_leading_bucket(
    async_client,
    db_setup,
    monkeypatch: pytest.MonkeyPatch,
):
    fixed_now = datetime(2026, 4, 3, 10, 37, 0)
    monkeypatch.setattr("app.modules.dashboard.service.utcnow", lambda: fixed_now)

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)
        logs_repo = RequestLogsRepository(session)

        await accounts_repo.upsert(_make_account("acc_partial", "partial@example.com"))
        await usage_repo.add_entry(
            "acc_partial",
            20.0,
            window="primary",
            recorded_at=fixed_now - timedelta(minutes=5),
        )
        await usage_repo.add_entry(
            "acc_partial",
            40.0,
            window="secondary",
            recorded_at=fixed_now - timedelta(minutes=2),
        )
        await logs_repo.add_log(
            account_id="acc_partial",
            request_id="req_partial_error",
            model="gpt-5.1",
            input_tokens=100,
            output_tokens=50,
            latency_ms=50,
            status="error",
            error_code="rate_limit_exceeded",
            requested_at=fixed_now - timedelta(hours=23, minutes=52),
        )

    response = await async_client.get("/api/dashboard/overview?timeframe=1d")
    assert response.status_code == 200

    payload = response.json()
    assert payload["summary"]["metrics"]["requests"] == 1
    assert payload["summary"]["metrics"]["tokens"] == 150
    assert payload["summary"]["metrics"]["errorCount"] == 1
    assert payload["summary"]["metrics"]["topError"] == "rate_limit_exceeded"
    assert all(point["v"] == 0 for point in payload["trends"]["requests"])
