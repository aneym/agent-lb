from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import delete

from app.db.models import Account, AccountStatus, RequestLog
from app.db.session import SessionLocal
from app.modules.accounts.usage_time_rollup import run_hourly_fold_pass
from app.modules.api_keys.repository import ApiKeysRepository

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_api_key_trends_keep_counted_history_and_exclude_edge_copies_after_pruning(db_setup):
    del db_setup
    base = datetime(2025, 1, 1)
    now = base + timedelta(days=5, minutes=37)
    async with SessionLocal() as session:
        session.add(
            Account(
                id="trend-retention",
                email="trend-retention@example.invalid",
                plan_type="plus",
                access_token_encrypted=b"access",
                refresh_token_encrypted=b"refresh",
                id_token_encrypted=b"id",
                last_refresh=base,
                status=AccountStatus.ACTIVE,
            )
        )
        await session.commit()
        for suffix, kind, source, input_tokens, output_tokens, reasoning_tokens in (
            ("counted", "normal", None, 100, 20, None),
            ("reasoning", "normal", None, 10, None, 5),
            ("edge", "normal", "edge:forwarded", 1000, 200, None),
            ("warmup", "warmup", None, 1000, 200, None),
            ("limit-warmup", "limit_warmup", None, 1000, 200, None),
        ):
            session.add(
                RequestLog(
                    account_id="trend-retention",
                    api_key_id="retained-key",
                    request_id=f"retention-{suffix}",
                    requested_at=base + timedelta(hours=2, minutes=15),
                    request_kind=kind,
                    source=source,
                    model="gpt-5.1",
                    status="success",
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    reasoning_tokens=reasoning_tokens,
                    cost_usd=0.25,
                )
            )
        await session.commit()
        repo = ApiKeysRepository(session)
        before = await repo.trends_by_key("retained-key", base, now)
        assert len(before) == 1
        assert before[0].total_tokens == 135
        assert before[0].total_cost_usd == 0.5
    await run_hourly_fold_pass(now=now)
    async with SessionLocal() as session:
        folded = await ApiKeysRepository(session).trends_by_key("retained-key", base, now)
        assert folded == before
        await session.execute(delete(RequestLog).where(RequestLog.requested_at < now - timedelta(days=1)))
        await session.commit()
        retained = await ApiKeysRepository(session).trends_by_key("retained-key", base, now)
        assert retained == before
