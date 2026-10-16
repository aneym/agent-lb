from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import text

from app.db.session import SessionLocal
from app.modules.quota_planner.repository import QuotaPlannerRepository
from app.modules.reports.repository import ReportsRepository


@pytest.mark.asyncio
async def test_request_log_aggregates_sort_in_memory_postgresql(db_setup):
    async with SessionLocal() as session:
        bind = session.get_bind()
        if bind is None or bind.dialect.name != "postgresql":
            pytest.skip("PostgreSQL-only work_mem test")
        await QuotaPlannerRepository(session).aggregate_demand_slots()
        assert (await session.execute(text("SHOW work_mem"))).scalar_one() == "256MB"
        await session.rollback()
        assert (await session.execute(text("SHOW work_mem"))).scalar_one() != "256MB"
        await ReportsRepository(session).aggregate_daily(datetime(2026, 1, 1), datetime(2026, 2, 1))
        assert (await session.execute(text("SHOW work_mem"))).scalar_one() == "256MB"
