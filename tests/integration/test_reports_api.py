from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.db.models import RequestLog
from app.db.session import SessionLocal

pytestmark = pytest.mark.integration

from tests.integration.reports_support import _make_account, _naive_utc


async def test_reports_api_returns_null_account_bucket(async_client, db_setup):
    start_at = _naive_utc(datetime(2026, 6, 1, 10, 0, 0, tzinfo=timezone.utc))
    async with SessionLocal() as session:
        session.add(_make_account("acc_reports", "reports@example.com"))
        session.add_all(
            [
                RequestLog(
                    account_id="acc_reports",
                    request_id="report-request-1",
                    requested_at=start_at,
                    model="gpt-5.1",
                    status="success",
                    input_tokens=12,
                    output_tokens=4,
                    cached_input_tokens=2,
                    cost_usd=0.35,
                ),
                RequestLog(
                    account_id=None,
                    request_id="report-request-2",
                    requested_at=start_at,
                    model="gpt-5.1",
                    status="success",
                    input_tokens=3,
                    output_tokens=1,
                    cached_input_tokens=0,
                    cost_usd=0.20,
                ),
            ]
        )
        await session.commit()

    response = await async_client.get(
        "/api/reports",
        params={
            "start_date": start_at.date().isoformat(),
            "end_date": start_at.date().isoformat(),
        },
    )
    assert response.status_code == 200

    payload = response.json()
    assert payload["daily"] == [
        {
            "activeAccounts": 1,
            "costUsd": 0.55,
            "cachedInputTokens": 2,
            "date": start_at.date().isoformat(),
            "errorCount": 0,
            "requests": 2,
            "inputTokens": 15,
            "outputTokens": 5,
        }
    ]
    assert payload["byAccount"] == [
        {
            "accountId": "acc_reports",
            "alias": None,
            "costUsd": 0.35,
            "requests": 1,
        },
        {
            "accountId": None,
            "alias": None,
            "costUsd": 0.2,
            "requests": 1,
        },
    ]


async def test_reports_api_includes_preserved_deleted_account_history(async_client, db_setup):
    start_at = _naive_utc(datetime(2026, 6, 1, 11, 0, 0, tzinfo=timezone.utc))
    deleted_at = _naive_utc(datetime(2026, 6, 2, 9, 0, 0, tzinfo=timezone.utc))
    async with SessionLocal() as session:
        session.add(
            RequestLog(
                account_id=None,
                request_id="report-deleted-account-history",
                requested_at=start_at,
                model="gpt-5.1",
                status="success",
                input_tokens=13,
                output_tokens=7,
                cached_input_tokens=3,
                cost_usd=0.42,
                deleted_at=deleted_at,
            )
        )
        await session.commit()

    response = await async_client.get(
        "/api/reports",
        params={
            "start_date": start_at.date().isoformat(),
            "end_date": start_at.date().isoformat(),
        },
    )
    assert response.status_code == 200

    payload = response.json()
    assert payload["summary"]["totalRequests"] == 1
    assert payload["summary"]["totalInputTokens"] == 13
    assert payload["summary"]["totalOutputTokens"] == 7
    assert payload["summary"]["totalCostUsd"] == 0.42
    assert payload["daily"] == [
        {
            "activeAccounts": 0,
            "costUsd": 0.42,
            "cachedInputTokens": 3,
            "date": start_at.date().isoformat(),
            "errorCount": 0,
            "requests": 1,
            "inputTokens": 13,
            "outputTokens": 7,
        }
    ]
    assert payload["byModel"] == [{"model": "gpt-5.1", "costUsd": 0.42, "percentage": 100.0}]
    assert payload["byAccount"] == [
        {
            "accountId": None,
            "alias": None,
            "costUsd": 0.42,
            "requests": 1,
        }
    ]


async def test_reports_api_hides_currently_canceled_subscription_accounts(async_client, db_setup):
    start_at = _naive_utc(datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc))
    active = _make_account("acc_reports_active_sub", "reports-active@example.com")
    canceled = _make_account("acc_reports_canceled_sub", "reports-canceled@example.com")
    canceled.subscription_status = " CANCELED "
    async with SessionLocal() as session:
        session.add_all([active, canceled])
        session.add_all(
            [
                RequestLog(
                    account_id=active.id,
                    request_id="report-active-subscription",
                    requested_at=start_at,
                    model="gpt-5.1",
                    status="success",
                    input_tokens=10,
                    output_tokens=5,
                    cached_input_tokens=1,
                    cost_usd=0.25,
                ),
                RequestLog(
                    account_id=canceled.id,
                    request_id="report-canceled-subscription",
                    requested_at=start_at,
                    model="gpt-5.1",
                    status="success",
                    input_tokens=99,
                    output_tokens=99,
                    cached_input_tokens=99,
                    cost_usd=9.99,
                ),
            ]
        )
        await session.commit()

    response = await async_client.get(
        "/api/reports",
        params={
            "start_date": start_at.date().isoformat(),
            "end_date": start_at.date().isoformat(),
        },
    )
    assert response.status_code == 200

    payload = response.json()
    assert payload["summary"]["totalRequests"] == 1
    assert payload["summary"]["activeAccounts"] == 1
    assert payload["summary"]["totalCostUsd"] == 0.25
    assert payload["daily"][0]["requests"] == 1
    assert payload["daily"][0]["activeAccounts"] == 1
    assert payload["byAccount"] == [
        {
            "accountId": active.id,
            "alias": None,
            "costUsd": 0.25,
            "requests": 1,
        }
    ]


async def test_reports_api_excludes_warmup_logs(async_client, db_setup):
    start_at = _naive_utc(datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc))
    async with SessionLocal() as session:
        session.add(_make_account("acc_reports_warmup", "reports-warmup@example.com"))
        session.add_all(
            [
                RequestLog(
                    account_id="acc_reports_warmup",
                    request_id="report-normal-traffic",
                    requested_at=start_at,
                    model="gpt-5.1",
                    status="success",
                    input_tokens=6,
                    output_tokens=4,
                    cached_input_tokens=0,
                    cost_usd=0.4,
                    source=None,
                ),
                RequestLog(
                    account_id="acc_reports_warmup",
                    request_id="report-warmup-source-traffic",
                    requested_at=start_at,
                    model="gpt-5.1",
                    status="success",
                    input_tokens=60,
                    output_tokens=40,
                    cached_input_tokens=0,
                    cost_usd=4.0,
                    source="limit_warmup",
                ),
                RequestLog(
                    account_id="acc_reports_warmup",
                    request_id="report-warmup-kind-traffic",
                    requested_at=start_at,
                    model="gpt-5.1",
                    status="success",
                    input_tokens=70,
                    output_tokens=50,
                    cached_input_tokens=0,
                    cost_usd=5.0,
                    source=None,
                    request_kind="warmup",
                ),
                RequestLog(
                    account_id="acc_reports_warmup",
                    request_id="report-limit-warmup-kind-traffic",
                    requested_at=start_at,
                    model="gpt-5.1",
                    status="success",
                    input_tokens=80,
                    output_tokens=60,
                    cached_input_tokens=0,
                    cost_usd=6.0,
                    source=None,
                    request_kind="limit_warmup",
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
    assert payload["summary"]["totalInputTokens"] == 6
    assert payload["summary"]["totalCostUsd"] == 0.4
    assert payload["byModel"] == [{"model": "gpt-5.1", "costUsd": 0.4, "percentage": 100.0}]
    assert payload["byAccount"] == [
        {
            "accountId": "acc_reports_warmup",
            "alias": None,
            "costUsd": 0.4,
            "requests": 1,
        }
    ]


async def test_reports_api_applies_account_and_model_filters(async_client, db_setup):
    start_at = _naive_utc(datetime(2026, 6, 1, 13, 0, 0, tzinfo=timezone.utc))
    async with SessionLocal() as session:
        session.add_all(
            [
                _make_account("acc_reports_filter_a", "reports-filter-a@example.com"),
                _make_account("acc_reports_filter_b", "reports-filter-b@example.com"),
            ]
        )
        session.add_all(
            [
                RequestLog(
                    account_id="acc_reports_filter_a",
                    request_id="report-filter-match",
                    requested_at=start_at,
                    model="gpt-5.1",
                    status="success",
                    input_tokens=8,
                    output_tokens=2,
                    cached_input_tokens=0,
                    cost_usd=0.8,
                ),
                RequestLog(
                    account_id="acc_reports_filter_a",
                    request_id="report-filter-wrong-model",
                    requested_at=start_at,
                    model="gpt-5.2",
                    status="success",
                    input_tokens=9,
                    output_tokens=2,
                    cached_input_tokens=0,
                    cost_usd=0.9,
                ),
                RequestLog(
                    account_id="acc_reports_filter_b",
                    request_id="report-filter-wrong-account",
                    requested_at=start_at,
                    model="gpt-5.1",
                    status="success",
                    input_tokens=10,
                    output_tokens=2,
                    cached_input_tokens=0,
                    cost_usd=1.0,
                ),
            ]
        )
        await session.commit()

    response = await async_client.get(
        "/api/reports",
        params={
            "start_date": "2026-06-01",
            "end_date": "2026-06-01",
            "account_id": "acc_reports_filter_a",
            "model": "gpt-5.1",
        },
    )
    assert response.status_code == 200

    payload = response.json()
    assert payload["summary"]["totalRequests"] == 1
    assert payload["summary"]["totalCostUsd"] == 0.8
    assert payload["byAccount"] == [
        {
            "accountId": "acc_reports_filter_a",
            "alias": None,
            "costUsd": 0.8,
            "requests": 1,
        }
    ]
    assert payload["byModel"] == [{"model": "gpt-5.1", "costUsd": 0.8, "percentage": 100.0}]


async def test_reports_api_includes_unpriced_models_in_model_breakdown(async_client, db_setup):
    start_at = _naive_utc(datetime(2026, 6, 1, 14, 0, 0, tzinfo=timezone.utc))
    async with SessionLocal() as session:
        session.add(_make_account("acc_reports_unpriced", "reports-unpriced@example.com"))
        session.add_all(
            [
                RequestLog(
                    account_id="acc_reports_unpriced",
                    request_id="report-priced-model",
                    requested_at=start_at,
                    model="gpt-priced",
                    status="success",
                    input_tokens=8,
                    output_tokens=2,
                    cached_input_tokens=0,
                    cost_usd=0.8,
                ),
                RequestLog(
                    account_id="acc_reports_unpriced",
                    request_id="report-unpriced-model",
                    requested_at=start_at,
                    model="gpt-unpriced",
                    status="success",
                    input_tokens=9,
                    output_tokens=2,
                    cached_input_tokens=0,
                    cost_usd=None,
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
    assert payload["summary"]["totalRequests"] == 2
    assert payload["byModel"] == [
        {"model": "gpt-priced", "costUsd": 0.8, "percentage": 100.0},
        {"model": "gpt-unpriced", "costUsd": 0.0, "percentage": 0.0},
    ]
    assert payload["byAccount"] == [
        {
            "accountId": "acc_reports_unpriced",
            "alias": None,
            "costUsd": 0.8,
            "requests": 2,
        }
    ]
