from __future__ import annotations

import os
import time
from datetime import datetime, timedelta

import pytest

from app.core.utils.time import naive_utc_to_epoch, utcnow
from app.db.models import AccountStatus
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.accounts.schemas import AccountSummary
from app.modules.dashboard.weekly_pace import _weekly_timing
from app.modules.usage.repository import UsageRepository
from tests.integration.dashboard_overview_support import (
    _make_account as _make_account,
)

pytestmark = pytest.mark.integration


def test_weekly_credit_pace_timing_treats_naive_reset_as_utc():
    if not hasattr(time, "tzset"):
        pytest.skip("tzset is required to simulate non-UTC local time")

    original_tz = os.environ.get("TZ")
    os.environ["TZ"] = "Asia/Seoul"
    time.tzset()
    try:
        fixed_now = datetime(2026, 5, 18, 12, 0, 0)
        reset_at = fixed_now + timedelta(days=4)
        now_ms = naive_utc_to_epoch(fixed_now) * 1000.0
        timing = _weekly_timing(
            AccountSummary(
                account_id="acc_tz",
                email="tz@example.com",
                display_name="tz@example.com",
                plan_type="pro",
                status="active",
                reset_at_secondary=reset_at,
                window_minutes_secondary=10080,
                capacity_credits_secondary=50_400.0,
                remaining_credits_secondary=40_320.0,
            ),
            now_ms,
        )
    finally:
        if original_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original_tz
        time.tzset()

    assert timing is not None
    assert timing[2] == pytest.approx(naive_utc_to_epoch(reset_at) * 1000.0)


@pytest.mark.asyncio
async def test_dashboard_projections_weekly_credit_pace_excludes_inactive_and_stale_accounts(
    async_client,
    db_setup,
    monkeypatch: pytest.MonkeyPatch,
):
    fixed_now = datetime(2026, 5, 18, 12, 0, 0)
    monkeypatch.setattr("app.modules.dashboard.service.utcnow", lambda: fixed_now)
    reset_at = int(naive_utc_to_epoch(fixed_now + timedelta(days=4)))

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)

        await accounts_repo.upsert(_make_account("acc_active_fresh", "fresh@example.com", plan_type="pro"))
        await accounts_repo.upsert(_make_account("acc_active_stale", "stale@example.com", plan_type="pro"))
        await accounts_repo.upsert(
            _make_account(
                "acc_inactive_fresh",
                "inactive@example.com",
                plan_type="pro",
                status=AccountStatus.DEACTIVATED,
            )
        )

        await usage_repo.add_entry(
            "acc_active_fresh",
            20.0,
            window="secondary",
            window_minutes=10080,
            reset_at=reset_at,
            recorded_at=fixed_now - timedelta(minutes=1),
        )
        await usage_repo.add_entry(
            "acc_active_stale",
            80.0,
            window="secondary",
            window_minutes=10080,
            reset_at=reset_at,
            recorded_at=fixed_now - timedelta(minutes=10),
        )
        await usage_repo.add_entry(
            "acc_inactive_fresh",
            90.0,
            window="secondary",
            window_minutes=10080,
            reset_at=reset_at,
            recorded_at=fixed_now - timedelta(minutes=1),
        )

    response = await async_client.get("/api/dashboard/projections")
    assert response.status_code == 200
    payload = response.json()

    pace = payload["weeklyCreditPace"]
    assert pace["accountCount"] == 1
    assert pace["staleAccountCount"] == 1
    assert pace["inactiveAccountCount"] == 1
    assert pace["totalFullCredits"] == pytest.approx(50_400.0)
    assert pace["actualUsedPercent"] == pytest.approx(20.0)
    assert pace["scheduledUsedPercent"] == pytest.approx(42.857, abs=0.01)
    assert pace["scheduleGapCredits"] == 0
    assert pace["status"] == "behind"


@pytest.mark.asyncio
async def test_dashboard_projections_weekly_credit_pace_forecast_uses_recent_slope_not_full_window_average(
    async_client,
    db_setup,
    monkeypatch: pytest.MonkeyPatch,
):
    fixed_now = datetime(2026, 5, 18, 12, 0, 0)
    monkeypatch.setattr("app.modules.dashboard.service.utcnow", lambda: fixed_now)
    reset_at = int(naive_utc_to_epoch(fixed_now + timedelta(days=5, hours=18)))

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)

        await accounts_repo.upsert(_make_account("acc_recent_flat", "flat@example.com", plan_type="pro"))
        await usage_repo.add_entry(
            "acc_recent_flat",
            0.0,
            window="secondary",
            window_minutes=10080,
            reset_at=reset_at,
            recorded_at=fixed_now - timedelta(days=1),
        )
        await usage_repo.add_entry(
            "acc_recent_flat",
            24.0,
            window="secondary",
            window_minutes=10080,
            reset_at=reset_at,
            recorded_at=fixed_now - timedelta(hours=3),
        )
        await usage_repo.add_entry(
            "acc_recent_flat",
            24.0,
            window="secondary",
            window_minutes=10080,
            reset_at=reset_at,
            recorded_at=fixed_now - timedelta(minutes=1),
        )

    response = await async_client.get("/api/dashboard/projections")
    assert response.status_code == 200
    payload = response.json()

    pace = payload["weeklyCreditPace"]
    assert pace["accountCount"] == 1
    assert pace["actualUsedPercent"] == pytest.approx(24.0)
    assert pace["scheduledUsedPercent"] == pytest.approx(17.857, abs=0.01)
    assert pace["scheduleGapCredits"] == pytest.approx(3_096.0, abs=1.0)
    assert pace["projectedShortfallCredits"] == 0
    assert pace["forecastBurnRateCreditsPerHour"] == pytest.approx(0.0)
    assert pace["paceMultiplier"] == pytest.approx(0.0)
    assert pace["pauseForBreakEvenHours"] is None
    assert pace["status"] == "ahead"


@pytest.mark.asyncio
async def test_dashboard_projections_weekly_credit_pace_uses_configured_working_days(
    async_client,
    db_setup,
    monkeypatch: pytest.MonkeyPatch,
):
    fixed_now = datetime(2026, 5, 24, 12, 0, 0)
    monkeypatch.setattr("app.modules.dashboard.service.utcnow", lambda: fixed_now)
    reset_at = int(naive_utc_to_epoch(datetime(2026, 5, 25, 0, 0, 0)))

    settings_response = await async_client.put("/api/settings", json={"weeklyPaceWorkingDays": "0,1,2,3,4"})
    assert settings_response.status_code == 200

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)

        await accounts_repo.upsert(_make_account("acc_weekdays", "weekdays@example.com", plan_type="pro"))
        await usage_repo.add_entry(
            "acc_weekdays",
            80.0,
            window="secondary",
            window_minutes=10080,
            reset_at=reset_at,
            recorded_at=fixed_now - timedelta(minutes=1),
        )

    response = await async_client.get("/api/dashboard/projections")
    assert response.status_code == 200
    payload = response.json()

    pace = payload["weeklyCreditPace"]
    assert pace["accountCount"] == 1
    assert pace["actualUsedPercent"] == pytest.approx(80.0)
    assert pace["scheduledUsedPercent"] == pytest.approx(100.0)
    assert pace["scheduleGapCredits"] == 0
    assert pace["status"] == "behind"


@pytest.mark.asyncio
async def test_dashboard_projections_compute_depletion_from_recent_db_history(async_client, db_setup):
    now = utcnow().replace(microsecond=0)

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)

        await accounts_repo.upsert(_make_account("acc_depletion", "depletion@example.com"))
        await usage_repo.add_entry(
            "acc_depletion",
            10.0,
            window="primary",
            window_minutes=60,
            reset_at=int(naive_utc_to_epoch(now + timedelta(minutes=45))),
            recorded_at=now - timedelta(minutes=20),
        )
        await usage_repo.add_entry(
            "acc_depletion",
            35.0,
            window="primary",
            window_minutes=60,
            reset_at=int(naive_utc_to_epoch(now + timedelta(minutes=45))),
            recorded_at=now - timedelta(minutes=5),
        )

    response = await async_client.get("/api/dashboard/projections")
    assert response.status_code == 200

    payload = response.json()
    assert payload["depletionPrimary"] is not None
    assert 0.0 <= payload["depletionPrimary"]["risk"] <= 1.0
    assert payload["depletionPrimary"]["riskLevel"] in {"safe", "warning", "danger", "critical"}


@pytest.mark.asyncio
async def test_dashboard_projections_pool_risk_is_not_worst_account_and_splits_by_provider(async_client, db_setup):
    now = utcnow().replace(microsecond=0)
    reset_at = int(naive_utc_to_epoch(now + timedelta(minutes=45)))

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)

        hot = _make_account("acc_hot", "hot@example.com")
        hot.provider = "anthropic"
        await accounts_repo.upsert(hot)
        for used, minutes_ago in ((10.0, 20), (80.0, 5)):
            await usage_repo.add_entry(
                "acc_hot",
                used,
                window="primary",
                window_minutes=60,
                reset_at=reset_at,
                recorded_at=now - timedelta(minutes=minutes_ago),
            )
        for index in range(4):
            calm = _make_account(f"acc_calm_{index}", f"calm{index}@example.com")
            calm.provider = "openai"
            await accounts_repo.upsert(calm)
            for used, minutes_ago in ((1.0, 20), (2.0, 5)):
                await usage_repo.add_entry(
                    f"acc_calm_{index}",
                    used,
                    window="primary",
                    window_minutes=60,
                    reset_at=reset_at,
                    recorded_at=now - timedelta(minutes=minutes_ago),
                )

    response = await async_client.get("/api/dashboard/projections")
    assert response.status_code == 200

    payload = response.json()
    depletion = payload["depletionPrimary"]
    assert depletion is not None

    assert depletion["riskLevel"] in {"safe", "warning"}
    assert depletion["worstAccountId"] == "acc_hot"
    assert depletion["worstAccountEmail"] == "hot@example.com"
    assert depletion["worstRiskLevel"] in {"danger", "critical"}
    assert depletion["accountCount"] == 5

    by_provider = payload["depletionPrimaryByProvider"]
    assert set(by_provider) == {"anthropic", "openai"}
    assert by_provider["anthropic"]["riskLevel"] in {"danger", "critical"}
    assert by_provider["anthropic"]["worstAccountId"] == "acc_hot"
    assert by_provider["openai"]["riskLevel"] == "safe"


@pytest.mark.asyncio
async def test_dashboard_projections_weekly_only_depletion_uses_current_stream(async_client, db_setup):
    now = utcnow().replace(microsecond=0)
    reset_at = int(naive_utc_to_epoch(now + timedelta(minutes=30)))

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)

        await accounts_repo.upsert(_make_account("acc_weekly_depletion", "weekly@example.com", plan_type="free"))

        await usage_repo.add_entry(
            "acc_weekly_depletion",
            0.0,
            window="secondary",
            window_minutes=10080,
            reset_at=reset_at,
            recorded_at=now - timedelta(days=6, minutes=2),
        )
        await usage_repo.add_entry(
            "acc_weekly_depletion",
            5.0,
            window="secondary",
            window_minutes=10080,
            reset_at=reset_at,
            recorded_at=now - timedelta(days=6, minutes=1),
        )
        await usage_repo.add_entry(
            "acc_weekly_depletion",
            6.0,
            window="primary",
            window_minutes=10080,
            reset_at=reset_at,
            recorded_at=now - timedelta(minutes=2),
        )
        await usage_repo.add_entry(
            "acc_weekly_depletion",
            7.0,
            window="primary",
            window_minutes=10080,
            reset_at=reset_at,
            recorded_at=now - timedelta(minutes=1),
        )

    response = await async_client.get("/api/dashboard/projections")
    assert response.status_code == 200

    payload = response.json()
    assert payload["depletionSecondary"] is not None
    assert payload["depletionSecondary"]["risk"] == pytest.approx(0.37, abs=0.02)
