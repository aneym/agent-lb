from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.db.models import RequestLog
from app.db.session import SessionLocal

pytestmark = pytest.mark.integration

from tests.integration.reports_support import _make_account, _naive_utc


async def test_reports_api_includes_end_date_until_next_midnight(async_client, db_setup):
    end_day_last_second = _naive_utc(datetime(2026, 6, 1, 23, 59, 59, tzinfo=timezone.utc))
    next_day_midnight = _naive_utc(datetime(2026, 6, 2, 0, 0, 0, tzinfo=timezone.utc))
    async with SessionLocal() as session:
        session.add(_make_account("acc_reports_end", "reports-end@example.com"))
        session.add_all(
            [
                RequestLog(
                    account_id="acc_reports_end",
                    request_id="report-end-included",
                    requested_at=end_day_last_second,
                    model="gpt-5.1",
                    status="success",
                    input_tokens=10,
                    output_tokens=5,
                    cached_input_tokens=0,
                    cost_usd=0.5,
                ),
                RequestLog(
                    account_id="acc_reports_end",
                    request_id="report-end-excluded",
                    requested_at=next_day_midnight,
                    model="gpt-5.1",
                    status="success",
                    input_tokens=99,
                    output_tokens=99,
                    cached_input_tokens=0,
                    cost_usd=9.9,
                ),
            ]
        )
        await session.commit()

    response = await async_client.get(
        "/api/reports",
        params={"start_date": "2026-06-01", "end_date": "2026-06-01"},
    )
    assert response.status_code == 200

    payload = response.json()
    assert payload["summary"]["totalRequests"] == 1
    assert payload["summary"]["totalCostUsd"] == 0.5
    assert payload["daily"][0]["date"] == "2026-06-01"


async def test_reports_api_default_range_uses_last_seven_calendar_days(async_client, db_setup, monkeypatch):
    fixed_now = datetime(2026, 6, 8, 10, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr("app.modules.reports.service.utcnow", lambda: fixed_now)
    async with SessionLocal() as session:
        session.add(_make_account("acc_reports_default", "reports-default@example.com"))
        session.add_all(
            [
                RequestLog(
                    account_id="acc_reports_default",
                    request_id="report-default-old",
                    requested_at=_naive_utc(datetime(2026, 6, 1, 23, 59, 59, tzinfo=timezone.utc)),
                    model="gpt-5.1",
                    status="success",
                    input_tokens=99,
                    output_tokens=99,
                    cached_input_tokens=0,
                    cost_usd=9.9,
                ),
                RequestLog(
                    account_id="acc_reports_default",
                    request_id="report-default-start",
                    requested_at=_naive_utc(datetime(2026, 6, 2, 0, 0, 0, tzinfo=timezone.utc)),
                    model="gpt-5.1",
                    status="success",
                    input_tokens=5,
                    output_tokens=1,
                    cached_input_tokens=0,
                    cost_usd=0.7,
                ),
                RequestLog(
                    account_id="acc_reports_default",
                    request_id="report-default-end",
                    requested_at=_naive_utc(datetime(2026, 6, 8, 23, 59, 59, tzinfo=timezone.utc)),
                    model="gpt-5.1",
                    status="success",
                    input_tokens=5,
                    output_tokens=1,
                    cached_input_tokens=0,
                    cost_usd=1.4,
                ),
                RequestLog(
                    account_id="acc_reports_default",
                    request_id="report-default-future",
                    requested_at=_naive_utc(datetime(2026, 6, 9, 0, 0, 0, tzinfo=timezone.utc)),
                    model="gpt-5.1",
                    status="success",
                    input_tokens=99,
                    output_tokens=99,
                    cached_input_tokens=0,
                    cost_usd=9.9,
                ),
            ]
        )
        await session.commit()

    response = await async_client.get("/api/reports")
    assert response.status_code == 200

    payload = response.json()
    assert payload["summary"]["totalRequests"] == 2
    assert payload["summary"]["avgRequestsPerDay"] == 0.29
    assert payload["daily"][0]["date"] == "2026-06-02"
    assert payload["daily"][1]["date"] == "2026-06-08"


async def test_reports_api_summary_counts_range_accounts_and_calendar_days(async_client, db_setup):
    async with SessionLocal() as session:
        session.add_all(
            [
                _make_account("acc_reports_sparse_a", "reports-sparse-a@example.com"),
                _make_account("acc_reports_sparse_b", "reports-sparse-b@example.com"),
            ]
        )
        session.add_all(
            [
                RequestLog(
                    account_id="acc_reports_sparse_a",
                    request_id="report-sparse-a",
                    requested_at=_naive_utc(datetime(2026, 6, 1, 10, 0, 0, tzinfo=timezone.utc)),
                    model="gpt-5.1",
                    status="success",
                    input_tokens=5,
                    output_tokens=1,
                    cached_input_tokens=0,
                    cost_usd=0.5,
                ),
                RequestLog(
                    account_id="acc_reports_sparse_b",
                    request_id="report-sparse-b",
                    requested_at=_naive_utc(datetime(2026, 6, 3, 10, 0, 0, tzinfo=timezone.utc)),
                    model="gpt-5.1",
                    status="success",
                    input_tokens=5,
                    output_tokens=1,
                    cached_input_tokens=0,
                    cost_usd=1.0,
                ),
            ]
        )
        await session.commit()

    response = await async_client.get(
        "/api/reports",
        params={"start_date": "2026-06-01", "end_date": "2026-06-03"},
    )
    assert response.status_code == 200

    payload = response.json()
    assert payload["summary"]["activeAccounts"] == 2
    assert payload["summary"]["avgCostPerDay"] == 0.5
    assert payload["summary"]["avgRequestsPerDay"] == 0.67
