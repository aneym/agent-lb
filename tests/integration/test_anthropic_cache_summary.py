from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.utils.time import utcnow
from app.db.session import SessionLocal
from app.modules.request_logs.repository import RequestLogsRepository

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_anthropic_cache_summary_filters_window_provider_and_warmups(async_client, db_setup):
    now = utcnow()
    rows = [
        ("current", "anthropic", "success", "normal", now, 20, 10, 70),
        ("old", "anthropic", "success", "normal", now - timedelta(hours=2), 100, 0, 0),
        ("openai", "openai", "success", "normal", now, 100, 0, 0),
        ("warmup", "anthropic", "success", "warmup", now, 100, 0, 0),
        ("error", "anthropic", "error", "normal", now, 100, 0, 0),
        ("missing", "anthropic", "success", "normal", now, 10, None, None),
    ]
    async with SessionLocal() as session:
        repo = RequestLogsRepository(session)
        for request_id, provider, status, kind, when, uncached, creation, read in rows:
            await repo.add_log(
                account_id=None,
                request_id=f"cache-summary-{request_id}",
                model="claude-test",
                input_tokens=uncached,
                output_tokens=0,
                latency_ms=1,
                status=status,
                error_code=None,
                requested_at=when,
                provider=provider,
                request_kind=kind,
                cache_creation_tokens=creation,
                cache_read_tokens=read,
            )
        await session.commit()

    response = await async_client.get("/api/request-logs/anthropic-cache-summary")
    assert response.status_code == 200
    assert response.json() == {
        "window_minutes": 60,
        "request_count": 2,
        "incomplete_request_count": 1,
        "input_tokens": 30,
        "cache_creation_tokens": 10,
        "cache_read_tokens": 70,
    }
