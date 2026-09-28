from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.sql.dml import Insert

from app.db.models import Account, StickySession, StickySessionKind
from app.db.session import SessionLocal
from app.modules.proxy.sticky_repository import StickySessionsRepository

pytestmark = pytest.mark.unit


async def _add_account(session) -> None:
    session.add(
        Account(
            id="race-account",
            email="race@example.invalid",
            plan_type="plus",
            access_token_encrypted=b"access",
            refresh_token_encrypted=b"refresh",
            last_refresh=datetime.now(timezone.utc).replace(tzinfo=None),
        )
    )
    await session.commit()


@pytest.mark.asyncio
async def test_upsert_retries_when_row_disappears_before_readback(db_setup, monkeypatch):
    async with SessionLocal() as session:
        await _add_account(session)
        repo = StickySessionsRepository(session)
        get_entry = repo.get_entry
        reads = 0

        async def delete_on_first_read(key, *, kind):
            nonlocal reads
            reads += 1
            if reads == 1:
                await repo.delete(key, kind=kind)
            return await get_entry(key, kind=kind)

        monkeypatch.setattr(repo, "get_entry", delete_on_first_read)
        row = await repo.upsert("raced-session", "race-account", kind=StickySessionKind.CODEX_SESSION)

        assert reads == 2
        assert row.account_id == "race-account"
        stored = (await session.execute(select(StickySession).where(StickySession.key == "raced-session"))).scalar_one()
        assert stored.account_id == "race-account"


@pytest.mark.asyncio
async def test_upsert_stops_after_three_missing_readbacks(db_setup, monkeypatch):
    async with SessionLocal() as session:
        await _add_account(session)
        repo = StickySessionsRepository(session)
        reads = 0
        writes = 0
        execute = session.execute

        async def missing_entry(key, *, kind):
            nonlocal reads
            reads += 1
            return None

        async def count_upserts(statement, *args, **kwargs):
            nonlocal writes
            if isinstance(statement, Insert) and statement.table.name == StickySession.__tablename__:
                writes += 1
            return await execute(statement, *args, **kwargs)

        monkeypatch.setattr(repo, "get_entry", missing_entry)
        monkeypatch.setattr(session, "execute", count_upserts)
        with pytest.raises(RuntimeError, match="StickySession upsert failed"):
            await repo.upsert("missing-session", "race-account", kind=StickySessionKind.CODEX_SESSION)

        assert reads == 3
        assert writes == 3
