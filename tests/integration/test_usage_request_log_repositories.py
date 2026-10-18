from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.utils.time import utcnow
from app.db.models import (
    RequestLog,
)
from app.db.session import SessionLocal
from app.modules.accounts.repository import (
    AccountsRepository,
)
from app.modules.request_logs.repository import RequestLogsRepository
from app.modules.usage.repository import UsageRepository
from tests.integration.repository_account_support import _make_account

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_usage_repository_aggregate(db_setup):
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        repo = UsageRepository(session)
        await accounts_repo.upsert(_make_account("acc1", "acc1@example.com"))
        await accounts_repo.upsert(_make_account("acc2", "acc2@example.com"))
        now = utcnow()
        await repo.add_entry("acc1", 10.0, recorded_at=now - timedelta(hours=1))
        await repo.add_entry("acc1", 30.0, recorded_at=now - timedelta(minutes=30))
        await repo.add_entry("acc2", 50.0, recorded_at=now - timedelta(minutes=10))

        rows = await repo.aggregate_since(now - timedelta(hours=5))
        row_map = {row.account_id: row for row in rows}
        assert row_map["acc1"].used_percent_avg == pytest.approx(20.0)
        assert row_map["acc2"].used_percent_avg == pytest.approx(50.0)


@pytest.mark.asyncio
async def test_request_logs_repository_filters(db_setup):
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_account("acc1", "acc1@example.com"))
        await accounts_repo.upsert(_make_account("acc2", "acc2@example.com"))
        now = utcnow()
        await repo.add_log(
            account_id="acc1",
            request_id="req_repo_1",
            model="gpt-5.1",
            input_tokens=10,
            output_tokens=20,
            latency_ms=100,
            status="success",
            error_code=None,
            requested_at=now - timedelta(minutes=10),
        )
        await repo.add_log(
            account_id="acc2",
            request_id="req_repo_2",
            model="gpt-5.1",
            input_tokens=5,
            output_tokens=5,
            latency_ms=50,
            status="error",
            error_code="rate_limit_exceeded",
            requested_at=now - timedelta(minutes=5),
        )

        results, total = await repo.list_recent(limit=0, account_ids=["acc1"])
        assert len(results) == 1
        assert results[0].account_id == "acc1"
        assert total == 1

        results, total = await repo.list_recent(limit=0, include_success=False)
        assert len(results) == 1
        assert results[0].error_code == "rate_limit_exceeded"
        assert total == 1


@pytest.mark.asyncio
async def test_request_logs_repository_persists_useragent_fields(db_setup):
    async with SessionLocal() as session:
        repo = RequestLogsRepository(session)

        await repo.add_log(
            account_id=None,
            request_id="req_useragent_full",
            model="gpt-5.1",
            input_tokens=3,
            output_tokens=7,
            latency_ms=42,
            status="success",
            error_code=None,
            useragent="opencode/1.15.13 ai-sdk/provider-utils/4.0.23 runtime/bun/1.3.14",
            useragent_group="opencode",
        )

        result = await session.execute(select(RequestLog).where(RequestLog.request_id == "req_useragent_full"))
        stored = result.scalar_one()
        assert stored.useragent == "opencode/1.15.13 ai-sdk/provider-utils/4.0.23 runtime/bun/1.3.14"
        assert stored.useragent_group == "opencode"


@pytest.mark.asyncio
async def test_request_logs_repository_preserves_null_useragent_fields(db_setup):
    async with SessionLocal() as session:
        repo = RequestLogsRepository(session)

        await repo.add_log(
            account_id=None,
            request_id="req_useragent_null",
            model="gpt-5.1",
            input_tokens=3,
            output_tokens=7,
            latency_ms=42,
            status="success",
            error_code=None,
            useragent="",
            useragent_group="",
        )

        result = await session.execute(select(RequestLog).where(RequestLog.request_id == "req_useragent_null"))
        stored = result.scalar_one()
        assert stored.useragent is None
        assert stored.useragent_group is None


@pytest.mark.asyncio
async def test_request_logs_repository_persists_null_useragent_fields_when_omitted(db_setup):
    async with SessionLocal() as session:
        repo = RequestLogsRepository(session)

        await repo.add_log(
            account_id=None,
            request_id="req_useragent_missing",
            model="gpt-5.1",
            input_tokens=3,
            output_tokens=7,
            latency_ms=42,
            status="success",
            error_code=None,
        )

        result = await session.execute(select(RequestLog).where(RequestLog.request_id == "req_useragent_missing"))
        stored = result.scalar_one()
        assert stored.useragent is None
        assert stored.useragent_group is None


@pytest.mark.asyncio
async def test_request_logs_repository_normalizes_whitespace_only_useragent_fields_to_null(db_setup):
    async with SessionLocal() as session:
        repo = RequestLogsRepository(session)

        await repo.add_log(
            account_id=None,
            request_id="req_useragent_whitespace",
            model="gpt-5.1",
            input_tokens=3,
            output_tokens=7,
            latency_ms=42,
            status="success",
            error_code=None,
            useragent=" \t\n ",
            useragent_group="   ",
        )

        result = await session.execute(select(RequestLog).where(RequestLog.request_id == "req_useragent_whitespace"))
        stored = result.scalar_one()
        assert stored.useragent is None
        assert stored.useragent_group is None
