from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import delete, select

from app.core.utils.time import utcnow
from app.db.models import Account, AccountUsageRollup, RequestLog
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.accounts.usage_rollup import run_fold_pass
from app.modules.request_logs.repository import RequestLogsRepository
from tests.floor.account_rollup_floor_support import _make_account

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_fold_and_retention_keep_cache_write_and_read_token_counts(db_setup):
    now = utcnow()
    old = now - timedelta(days=60)
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        await repo.upsert(_make_account("cache-tokens", "cache@example.invalid"))
        logs = RequestLogsRepository(session)
        for request_id, at, cache_write, cache_read, source in (
            ("old", old, 999, 999, None),
            ("old", old, 10, 20, None),
            ("live", now, 30, 40, None),
            ("edge-receipt", old, 500, 500, "edge:replica-1"),
        ):
            await logs.add_log(
                account_id="cache-tokens",
                request_id=request_id,
                model="claude-test",
                input_tokens=100,
                output_tokens=50,
                cache_creation_tokens=cache_write,
                cache_read_tokens=cache_read,
                latency_ms=100,
                status="success",
                error_code=None,
                source=source,
                requested_at=at,
            )
        before = (await repo.list_request_usage_summary_by_account())["cache-tokens"]
        assert before.request_count == 2
        assert before.cache_creation_tokens == 40
        assert before.cache_read_tokens == 60
    await run_fold_pass(now=now)
    async with SessionLocal() as session:
        folded = await session.get(AccountUsageRollup, "cache-tokens")
        assert folded is not None
        assert folded.cache_creation_tokens == 10
        assert folded.cache_read_tokens == 20
        await session.execute(delete(RequestLog).where(RequestLog.requested_at < now - timedelta(days=30)))
        await session.commit()
    async with SessionLocal() as session:
        assert len((await session.execute(select(RequestLog))).scalars().all()) == 1
        after = (await AccountsRepository(session).list_request_usage_summary_by_account())["cache-tokens"]
        assert after == before


async def test_identity_consolidation_merges_folded_cache_token_counts(db_setup):
    now = utcnow()
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        for account_id in ("canonical-cache", "duplicate-cache"):
            await repo.upsert(
                _make_account(account_id, "same@example.invalid", chatgpt_account_id="workspace-cache"),
                merge_by_email=False,
            )
            await RequestLogsRepository(session).add_log(
                account_id=account_id,
                request_id=account_id,
                model="claude-test",
                input_tokens=100,
                output_tokens=50,
                cache_creation_tokens=10,
                cache_read_tokens=20,
                latency_ms=100,
                status="success",
                error_code=None,
                requested_at=now - timedelta(days=2),
            )
    await run_fold_pass(now=now)
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        saved = await repo.upsert(
            _make_account("canonical-cache", "same@example.invalid", chatgpt_account_id="workspace-cache"),
            merge_by_email=False,
            merge_by_chatgpt_identity=True,
        )
        folded = await session.get(AccountUsageRollup, saved.id)
        assert folded is not None
        assert folded.cache_creation_tokens == 20
        assert folded.cache_read_tokens == 40
        assert len((await session.execute(select(Account))).scalars().all()) == 1
