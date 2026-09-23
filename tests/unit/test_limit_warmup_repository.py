from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import Base
from app.modules.limit_warmup.repository import LimitWarmupRepository

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_continuous_claim_retries_failed_send_and_survives_repository_restart() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        now = datetime(2026, 9, 23, 20, 0, 10)
        async with sessions() as session:
            repo = LimitWarmupRepository(session)
            first = await repo.claim_continuous_attempt(
                account_id="account-1", reset_at=1_790_193_600, model="claude-haiku-4-5", now=now
            )
            assert first is not None
            await repo.complete_attempt(first.id, status="failed", completed_at=now, error_code="upstream_503")

        async with sessions() as session:
            repo = LimitWarmupRepository(session)
            assert (
                await repo.claim_continuous_attempt(
                    account_id="account-1",
                    reset_at=1_790_193_600,
                    model="claude-haiku-4-5",
                    now=now + timedelta(seconds=29),
                )
                is None
            )
            retry = await repo.claim_continuous_attempt(
                account_id="account-1",
                reset_at=1_790_193_600,
                model="claude-haiku-4-5",
                now=now + timedelta(seconds=30),
            )
            assert retry is not None and retry.id == first.id
            assert retry.retry_count == 1
            await repo.complete_attempt(retry.id, status="succeeded", completed_at=now + timedelta(seconds=31))

        async with sessions() as session:
            repo = LimitWarmupRepository(session)
            assert (
                await repo.claim_continuous_attempt(
                    account_id="account-1",
                    reset_at=1_790_193_600,
                    model="claude-haiku-4-5",
                    now=now + timedelta(minutes=10),
                )
                is None
            )
            assert await repo.last_primed_by_account(["account-1"]) == {"account-1": now + timedelta(seconds=31)}
    finally:
        await engine.dispose()
