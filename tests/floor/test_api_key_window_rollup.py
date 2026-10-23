from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import delete

from app.db.models import RequestLog
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.accounts.usage_time_rollup import run_hourly_fold_pass
from app.modules.api_keys.repository import ApiKeysRepository
from tests.floor.account_rollup_floor_support import _make_account

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_seven_day_totals_and_account_costs_survive_retention(db_setup):
    now = datetime(2026, 10, 22, 12)
    since = now - timedelta(days=7)
    async with SessionLocal() as session:
        await AccountsRepository(session).upsert(_make_account("key-window", "window@example.invalid"))
        for index, (at, account_id, deleted, kind) in enumerate(
            (
                (since + timedelta(hours=1), "key-window", None, "normal"),
                (since + timedelta(days=1), None, since, "normal"),
                (now - timedelta(minutes=5), "key-window", None, "normal"),
                (since + timedelta(hours=1), "key-window", None, "warmup"),
            )
        ):
            session.add(
                RequestLog(
                    request_id=f"key-window-{index}",
                    account_id=account_id,
                    api_key_id="window-key",
                    model="gpt-5.1",
                    status="success",
                    requested_at=at,
                    deleted_at=deleted,
                    request_kind=kind,
                    input_tokens=100,
                    output_tokens=None,
                    reasoning_tokens=25,
                    cached_input_tokens=20,
                    cost_usd=0.1,
                )
            )
        await session.commit()
    async with SessionLocal() as session:
        repo = ApiKeysRepository(session)
        before = await repo.usage_7d("window-key", since, now)
        costs = await repo.usage_7d_by_account("window-key", since, now)
        assert before.total_requests == 3
        assert before.total_tokens == 375
        assert before.cached_input_tokens == 60
        assert before.total_cost_usd == 0.3
        assert costs == before.account_costs
        assert len(costs) == 2
        assert costs[0].account_id == "key-window"
        assert costs[1].is_deleted
    await run_hourly_fold_pass(now=now)
    async with SessionLocal() as session:
        assert await ApiKeysRepository(session).usage_7d("window-key", since, now) == before
        await session.execute(delete(RequestLog).where(RequestLog.requested_at < now - timedelta(days=2)))
        await session.commit()
    async with SessionLocal() as session:
        repo = ApiKeysRepository(session)
        assert await repo.usage_7d("window-key", since, now) == before
        assert await repo.usage_7d_by_account("window-key", since, now) == costs
        empty = await repo.usage_7d("missing-key", since, now)
        assert empty.total_requests == 0
        assert empty.account_costs == []
