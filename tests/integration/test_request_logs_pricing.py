from __future__ import annotations

import pytest
from sqlalchemy import update

from app.core.utils.time import utcnow
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.request_logs.repository import RequestLogsRepository

pytestmark = pytest.mark.integration

from tests.integration.request_logs_support import _make_account


def _cost(
    input_tokens: int,
    output_tokens: int,
    cached_tokens: int = 0,
    *,
    input_rate: float = 1.25,
    cached_rate: float = 0.125,
    output_rate: float = 10.0,
) -> float:
    billable = input_tokens - cached_tokens
    return (
        (billable / 1_000_000) * input_rate
        + (cached_tokens / 1_000_000) * cached_rate
        + (output_tokens / 1_000_000) * output_rate
    )


@pytest.mark.asyncio
async def test_request_logs_tokens_and_cost_use_reasoning_tokens(async_client, db_setup):
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_account("acc_reason", "reason@example.com"))

        await logs_repo.add_log(
            account_id="acc_reason",
            request_id="req_reason_1",
            model="gpt-5.1",
            input_tokens=1000,
            output_tokens=None,
            cached_input_tokens=100,
            reasoning_tokens=400,
            reasoning_effort="xhigh",
            latency_ms=50,
            status="success",
            error_code=None,
            requested_at=now,
        )

    response = await async_client.get("/api/request-logs?accountId=acc_reason&limit=1")
    assert response.status_code == 200
    payload = response.json()["requests"]
    assert len(payload) == 1
    entry = payload[0]
    assert entry["tokens"] == 1400
    assert entry["inputTokens"] == 1000
    assert entry["outputTokens"] == 400
    assert entry["cachedInputTokens"] == 100
    assert entry["reasoningEffort"] == "xhigh"
    expected = round(_cost(1000, 400, 100), 6)
    assert entry["costUsd"] == pytest.approx(expected)
    assert entry["costBreakdown"]["inputUsd"] == pytest.approx(round((900 / 1_000_000) * 1.25, 6))
    assert entry["costBreakdown"]["cachedInputUsd"] == pytest.approx(round((100 / 1_000_000) * 0.125, 6))
    assert entry["costBreakdown"]["outputUsd"] == pytest.approx(round((400 / 1_000_000) * 10.0, 6))
    assert entry["costBreakdown"]["totalUsd"] == pytest.approx(expected)


@pytest.mark.asyncio
async def test_request_logs_partial_rows_keep_nullable_cost_breakdown_shape(async_client, db_setup):
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_account("acc_partial_log", "partial-log@example.com"))

        await logs_repo.add_log(
            account_id="acc_partial_log",
            request_id="req_partial_log_1",
            model="gpt-5.1",
            input_tokens=1000,
            output_tokens=None,
            cached_input_tokens=100,
            reasoning_tokens=None,
            latency_ms=50,
            status="success",
            error_code=None,
            requested_at=now,
        )

    response = await async_client.get("/api/request-logs?accountId=acc_partial_log&limit=1")
    assert response.status_code == 200
    payload = response.json()["requests"]
    assert len(payload) == 1
    entry = payload[0]
    assert entry["inputTokens"] == 1000
    assert entry["outputTokens"] is None
    assert entry["costUsd"] is None
    assert entry["costBreakdown"] == {
        "inputUsd": pytest.approx(round((900 / 1_000_000) * 1.25, 6)),
        "cachedInputUsd": pytest.approx(round((100 / 1_000_000) * 0.125, 6)),
        "outputUsd": None,
        "totalUsd": None,
    }


@pytest.mark.asyncio
async def test_request_logs_cost_uses_computed_total_when_persisted_cost_missing(async_client, db_setup):
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_account("acc_missing_persisted_cost", "missing-persisted-cost@example.com"))

        log = await logs_repo.add_log(
            account_id="acc_missing_persisted_cost",
            request_id="req_missing_persisted_cost_1",
            model="gpt-5.1",
            input_tokens=1000,
            output_tokens=500,
            latency_ms=50,
            status="success",
            error_code=None,
            requested_at=now,
        )
        await session.execute(update(log.__class__).where(log.__class__.id == log.id).values(cost_usd=None))
        await session.commit()

    response = await async_client.get("/api/request-logs?accountId=acc_missing_persisted_cost&limit=1")
    assert response.status_code == 200
    payload = response.json()["requests"]
    assert len(payload) == 1
    expected = round(_cost(1000, 500), 6)
    assert payload[0]["costUsd"] == pytest.approx(expected)
    assert payload[0]["costBreakdown"]["totalUsd"] == pytest.approx(expected)


async def test_request_logs_cost_uses_priority_service_tier(async_client, db_setup):
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_account("acc_priority", "priority@example.com"))

        await logs_repo.add_log(
            account_id="acc_priority",
            request_id="req_priority_1",
            model="gpt-5.4",
            service_tier="priority",
            input_tokens=1_000_000,
            output_tokens=1_000_000,
            latency_ms=50,
            status="success",
            error_code=None,
            requested_at=now,
        )

    response = await async_client.get("/api/request-logs?accountId=acc_priority&limit=1")
    assert response.status_code == 200
    payload = response.json()["requests"]
    assert len(payload) == 1
    entry = payload[0]
    assert entry["serviceTier"] == "priority"
    expected = round(_cost(1_000_000, 1_000_000, input_rate=5.0, cached_rate=0.5, output_rate=30.0), 6)
    assert entry["costUsd"] == pytest.approx(expected)


@pytest.mark.asyncio
async def test_request_logs_cost_uses_flex_service_tier(async_client, db_setup):
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_account("acc_flex", "flex@example.com"))

        await logs_repo.add_log(
            account_id="acc_flex",
            request_id="req_flex_1",
            model="gpt-5.4",
            service_tier="flex",
            input_tokens=300_000,
            output_tokens=100_000,
            cached_input_tokens=50_000,
            latency_ms=50,
            status="success",
            error_code=None,
            requested_at=now,
        )

    response = await async_client.get("/api/request-logs?accountId=acc_flex&limit=1")
    assert response.status_code == 200
    payload = response.json()["requests"]
    assert len(payload) == 1
    entry = payload[0]
    assert entry["serviceTier"] == "flex"
    expected = round(_cost(300_000, 100_000, 50_000, input_rate=2.5, cached_rate=0.25, output_rate=11.25), 6)
    assert entry["costUsd"] == pytest.approx(expected)
    assert entry["costBreakdown"]["outputUsd"] == pytest.approx(round((100_000 / 1_000_000) * 11.25, 6))


@pytest.mark.asyncio
async def test_request_logs_cost_uses_persisted_cost_field(async_client, db_setup):
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_account("acc_persisted_log_cost", "persisted-log-cost@example.com"))

        log = await logs_repo.add_log(
            account_id="acc_persisted_log_cost",
            request_id="req_persisted_log_cost_1",
            model="gpt-5.1",
            input_tokens=1000,
            output_tokens=500,
            latency_ms=50,
            status="success",
            error_code=None,
            requested_at=now,
        )
        await session.execute(update(log.__class__).where(log.__class__.id == log.id).values(cost_usd=4.321234))
        await session.commit()

    response = await async_client.get("/api/request-logs?accountId=acc_persisted_log_cost&limit=1")
    assert response.status_code == 200
    payload = response.json()["requests"]
    assert len(payload) == 1
    assert payload[0]["costUsd"] == pytest.approx(4.321234)
    assert payload[0]["costBreakdown"]["inputUsd"] is None
    assert payload[0]["costBreakdown"]["cachedInputUsd"] is None
    assert payload[0]["costBreakdown"]["outputUsd"] is None
    assert payload[0]["costBreakdown"]["totalUsd"] == pytest.approx(4.321234)
