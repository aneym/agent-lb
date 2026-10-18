from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import update

from app.core.utils.time import utcnow
from app.db.models import RequestLog
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.request_logs.repository import RequestLogsRepository
from tests.integration.accounts_api_helpers import _make_account as _make_account

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_accounts_list_includes_request_usage_cost_rollup(async_client, db_setup):
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)

        await accounts_repo.upsert(_make_account("acc_cost", "cost@example.com"))
        await accounts_repo.upsert(_make_account("acc_other", "other@example.com"))

        await logs_repo.add_log(
            account_id="acc_cost",
            request_id="req_cost_1",
            model="gpt-5.3-codex",
            input_tokens=100_000,
            output_tokens=20_000,
            cached_input_tokens=90_000,
            latency_ms=200,
            status="success",
            error_code=None,
        )
        await logs_repo.add_log(
            account_id="acc_cost",
            request_id="req_cost_2",
            model="gpt-5.1-codex",
            input_tokens=50_000,
            output_tokens=10_000,
            cached_input_tokens=0,
            latency_ms=180,
            status="success",
            error_code=None,
        )
        await logs_repo.add_log(
            account_id="acc_other",
            request_id="req_other_1",
            model="gpt-5.1-codex-mini",
            input_tokens=1_000,
            output_tokens=500,
            cached_input_tokens=0,
            latency_ms=150,
            status="success",
            error_code=None,
        )

    response = await async_client.get("/api/accounts?fresh=1")
    assert response.status_code == 200
    payload = response.json()
    accounts = {item["accountId"]: item for item in payload["accounts"]}

    request_usage = accounts["acc_cost"]["requestUsage"]
    assert request_usage is not None
    assert request_usage["requestCount"] == 2
    assert request_usage["totalTokens"] == 180_000
    assert request_usage["cachedInputTokens"] == 90_000
    assert request_usage["totalCostUsd"] == pytest.approx(0.47575, abs=1e-6)

    other_usage = accounts["acc_other"]["requestUsage"]
    assert other_usage is not None
    assert other_usage["requestCount"] == 1
    assert other_usage["totalTokens"] == 1_500


@pytest.mark.asyncio
async def test_accounts_list_defers_request_usage_unless_fresh(async_client, db_setup):
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_account("acc_defer", "defer@example.com"))
        await logs_repo.add_log(
            account_id="acc_defer",
            request_id="req_defer_1",
            model="gpt-5.1-codex",
            input_tokens=1_000,
            output_tokens=500,
            cached_input_tokens=0,
            latency_ms=150,
            status="success",
            error_code=None,
        )

    default_response = await async_client.get("/api/accounts")
    assert default_response.status_code == 200
    default_accounts = {item["accountId"]: item for item in default_response.json()["accounts"]}
    assert default_accounts["acc_defer"]["requestUsage"] is None

    fresh_response = await async_client.get("/api/accounts?fresh=1")
    assert fresh_response.status_code == 200
    fresh_accounts = {item["accountId"]: item for item in fresh_response.json()["accounts"]}
    fresh_usage = fresh_accounts["acc_defer"]["requestUsage"]
    assert fresh_usage is not None
    assert fresh_usage["requestCount"] == 1
    assert fresh_usage["totalTokens"] == 1_500


@pytest.mark.asyncio
async def test_accounts_list_request_usage_cost_rollup_respects_service_tier(async_client, db_setup):
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)

        await accounts_repo.upsert(_make_account("acc_priority_cost", "priority-cost@example.com"))

        await logs_repo.add_log(
            account_id="acc_priority_cost",
            request_id="req_priority_cost_1",
            model="gpt-5.4",
            service_tier="priority",
            input_tokens=1_000_000,
            output_tokens=1_000_000,
            latency_ms=200,
            status="success",
            error_code=None,
        )

    response = await async_client.get("/api/accounts?fresh=1")
    assert response.status_code == 200
    payload = response.json()
    accounts = {item["accountId"]: item for item in payload["accounts"]}

    request_usage = accounts["acc_priority_cost"]["requestUsage"]
    assert request_usage is not None
    assert request_usage["requestCount"] == 1
    assert request_usage["totalTokens"] == 2_000_000
    assert request_usage["cachedInputTokens"] == 0
    assert request_usage["totalCostUsd"] == pytest.approx(35.0, abs=1e-6)


@pytest.mark.asyncio
async def test_accounts_list_request_usage_uses_persisted_cost(async_client, db_setup):
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)

        await accounts_repo.upsert(_make_account("acc_persisted_cost", "persisted-cost@example.com"))

        log = await logs_repo.add_log(
            account_id="acc_persisted_cost",
            request_id="req_persisted_cost_1",
            model="gpt-5.1",
            input_tokens=10,
            output_tokens=5,
            latency_ms=50,
            status="success",
            error_code=None,
        )
        await session.execute(update(log.__class__).where(log.__class__.id == log.id).values(cost_usd=12.345678))
        await session.commit()

    response = await async_client.get("/api/accounts?fresh=1")
    assert response.status_code == 200
    payload = response.json()
    accounts = {item["accountId"]: item for item in payload["accounts"]}

    request_usage = accounts["acc_persisted_cost"]["requestUsage"]
    assert request_usage is not None
    assert request_usage["totalCostUsd"] == pytest.approx(12.345678, abs=1e-6)


@pytest.mark.asyncio
async def test_accounts_list_request_usage_deduplicates_request_id_rows(async_client, db_setup):
    requested_at = utcnow()

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)

        await accounts_repo.upsert(_make_account("acc_replayed", "replayed@example.com"))

        first_attempt = await logs_repo.add_log(
            account_id="acc_replayed",
            request_id="req_repeat_1",
            model="gpt-5.1-codex",
            input_tokens=50_000,
            output_tokens=20_000,
            cached_input_tokens=10_000,
            latency_ms=180,
            status="success",
            error_code=None,
            requested_at=requested_at,
        )
        second_attempt = await logs_repo.add_log(
            account_id="acc_replayed",
            request_id="req_repeat_1",
            model="gpt-5.1-codex",
            input_tokens=5_000,
            output_tokens=1_000,
            cached_input_tokens=0,
            latency_ms=140,
            status="success",
            error_code=None,
            requested_at=requested_at,
        )
        await session.execute(update(RequestLog).where(RequestLog.id == first_attempt.id).values(cost_usd=5.0))
        await session.execute(update(RequestLog).where(RequestLog.id == second_attempt.id).values(cost_usd=2.0))
        await session.commit()

    response = await async_client.get("/api/accounts?fresh=1")
    assert response.status_code == 200
    payload = response.json()
    accounts = {item["accountId"]: item for item in payload["accounts"]}

    request_usage = accounts["acc_replayed"]["requestUsage"]
    assert request_usage is not None
    assert request_usage["requestCount"] == 1
    assert request_usage["totalTokens"] == 6_000
    assert request_usage["cachedInputTokens"] == 0
    assert request_usage["totalCostUsd"] == pytest.approx(2.0, abs=1e-6)


@pytest.mark.asyncio
async def test_accounts_list_request_usage_counts_repeated_request_id_by_requested_at(async_client, db_setup):
    requested_at = utcnow()
    repeated_request_time = requested_at + timedelta(seconds=30)

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)

        await accounts_repo.upsert(_make_account("acc_replayed_time", "replayed-time@example.com"))

        newer_attempt = await logs_repo.add_log(
            account_id="acc_replayed_time",
            request_id="req_repeat_time",
            model="gpt-5.1-codex",
            input_tokens=8_000,
            output_tokens=2_000,
            cached_input_tokens=1_000,
            latency_ms=180,
            status="success",
            error_code=None,
            requested_at=repeated_request_time,
        )
        older_backfill = await logs_repo.add_log(
            account_id="acc_replayed_time",
            request_id="req_repeat_time",
            model="gpt-5.1-codex",
            input_tokens=50_000,
            output_tokens=20_000,
            cached_input_tokens=10_000,
            latency_ms=140,
            status="success",
            error_code=None,
            requested_at=requested_at,
        )
        await session.execute(update(RequestLog).where(RequestLog.id == newer_attempt.id).values(cost_usd=3.0))
        await session.execute(update(RequestLog).where(RequestLog.id == older_backfill.id).values(cost_usd=9.0))
        await session.commit()

    response = await async_client.get("/api/accounts?fresh=1")
    assert response.status_code == 200
    payload = response.json()
    accounts = {item["accountId"]: item for item in payload["accounts"]}

    request_usage = accounts["acc_replayed_time"]["requestUsage"]
    assert request_usage is not None
    assert request_usage["requestCount"] == 2
    assert request_usage["totalTokens"] == 80_000
    assert request_usage["cachedInputTokens"] == 11_000
    assert request_usage["totalCostUsd"] == pytest.approx(12.0, abs=1e-6)


@pytest.mark.asyncio
async def test_accounts_list_request_usage_includes_anthropic_cache_tokens(async_client, db_setup):
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)

        account = _make_account("acc_anthropic_usage", "anthropic-usage@example.com")
        account.provider = "anthropic"
        await accounts_repo.upsert(account)

        await logs_repo.add_log(
            account_id="acc_anthropic_usage",
            provider="anthropic",
            request_id="req_anthropic_usage_1",
            model="claude-sonnet-4-5-20250929",
            input_tokens=1_000,
            output_tokens=500,
            cached_input_tokens=0,
            cache_creation_tokens=123,
            cache_read_tokens=456,
            latency_ms=150,
            status="success",
            error_code=None,
        )

    response = await async_client.get("/api/accounts?fresh=1")
    assert response.status_code == 200
    payload = response.json()
    accounts = {item["accountId"]: item for item in payload["accounts"]}

    request_usage = accounts["acc_anthropic_usage"]["requestUsage"]
    assert request_usage is not None
    assert request_usage["requestCount"] == 1
    assert request_usage["totalTokens"] == 1_500
    assert request_usage["cacheCreationTokens"] == 123
    assert request_usage["cacheReadTokens"] == 456
