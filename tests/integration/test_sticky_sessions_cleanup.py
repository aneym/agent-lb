from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import text

from app.core.utils.time import utcnow
from app.db.models import StickySessionKind
from app.db.session import SessionLocal
from app.modules.proxy.durable_bridge_repository import DurableBridgeRepository
from app.modules.sticky_sessions.cleanup_scheduler import StickySessionCleanupScheduler
from tests.integration.sticky_session_support import (
    _create_accounts,
    _insert_http_bridge_session,
    _insert_sticky_session,
    _set_affinity_ttl,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_sticky_sessions_cleanup_scheduler_removes_stale_prompt_cache_and_closed_bridge_sessions(db_setup):
    accounts = await _create_accounts()
    await _set_affinity_ttl(60)
    await _insert_sticky_session(
        key="cleanup-stale",
        account_id=accounts[0].id,
        kind=StickySessionKind.PROMPT_CACHE,
        updated_at_offset_seconds=600,
    )
    await _insert_sticky_session(
        key="cleanup-durable",
        account_id=accounts[1].id,
        kind=StickySessionKind.STICKY_THREAD,
        updated_at_offset_seconds=600,
    )
    await _insert_http_bridge_session(
        session_id="closed-stale-session",
        state="closed",
        last_seen_offset_seconds=600,
    )
    await _insert_http_bridge_session(
        session_id="active-stale-session",
        state="active",
        last_seen_offset_seconds=600,
    )
    await _insert_http_bridge_session(
        session_id="closed-fresh-session",
        state="closed",
        last_seen_offset_seconds=10,
    )

    scheduler = StickySessionCleanupScheduler(interval_seconds=300, enabled=True)
    await scheduler._cleanup_once()

    async with SessionLocal() as session:
        remaining = {
            row[0] for row in (await session.execute(text("SELECT key FROM sticky_sessions ORDER BY key"))).fetchall()
        }
        bridge_remaining = {
            row[0]
            for row in (await session.execute(text("SELECT id FROM http_bridge_sessions ORDER BY id"))).fetchall()
        }
        alias_remaining = {
            row[0]
            for row in (
                await session.execute(text("SELECT session_id FROM http_bridge_session_aliases ORDER BY session_id"))
            ).fetchall()
        }

    assert remaining == {"cleanup-durable"}
    assert bridge_remaining == {"active-stale-session", "closed-fresh-session"}
    assert alias_remaining == {"active-stale-session", "closed-fresh-session"}


@pytest.mark.asyncio
async def test_durable_bridge_closed_session_purge_drains_multiple_batches(db_setup):
    for index in range(3):
        await _insert_http_bridge_session(
            session_id=f"closed-batched-session-{index}",
            state="closed",
            last_seen_offset_seconds=600,
        )
    await _insert_http_bridge_session(
        session_id="active-batched-session",
        state="active",
        last_seen_offset_seconds=600,
    )

    async with SessionLocal() as session:
        repo = DurableBridgeRepository(session)
        deleted = await repo.purge_closed_before(utcnow() - timedelta(seconds=60), batch_size=2)

    async with SessionLocal() as session:
        bridge_remaining = {
            row[0]
            for row in (await session.execute(text("SELECT id FROM http_bridge_sessions ORDER BY id"))).fetchall()
        }
        alias_remaining = {
            row[0]
            for row in (
                await session.execute(text("SELECT session_id FROM http_bridge_session_aliases ORDER BY session_id"))
            ).fetchall()
        }

    assert deleted == 3
    assert bridge_remaining == {"active-batched-session"}
    assert alias_remaining == {"active-batched-session"}
