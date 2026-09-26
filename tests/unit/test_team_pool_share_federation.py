from __future__ import annotations

from datetime import timedelta, timezone

import pytest

from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, ApiKey, RequestLog, TeamMember, UsageHistory
from app.db.session import SessionLocal
from app.modules.team.pool_share import pool_share_windows, reset_pool_share_cache

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
@pytest.mark.parametrize("remote_extra_cost", [0, 20, 200])
async def test_local_member_share_never_understates_when_only_other_traffic_is_remote(db_setup, remote_extra_cost):
    del db_setup
    now = utcnow().replace(microsecond=0)
    reset = now + timedelta(days=3)
    async with SessionLocal() as session:
        session.add(TeamMember(id="member", name="Member", pool_share_percent=50))
        session.add(
            Account(
                id="account",
                email="account@example.invalid",
                plan_type="plus",
                access_token_encrypted=b"a",
                refresh_token_encrypted=b"b",
                last_refresh=now,
                status=AccountStatus.ACTIVE,
            )
        )
        await session.flush()
        session.add(ApiKey(id="member-key", name="Member", member_id="member", key_hash="hash", key_prefix="sk-clb"))
        session.add(
            UsageHistory(
                account_id="account",
                window="primary",
                used_percent=80,
                window_minutes=10080,
                reset_at=int(reset.replace(tzinfo=timezone.utc).timestamp()),
                recorded_at=now,
            )
        )
        session.add_all(
            [
                RequestLog(
                    account_id="account",
                    api_key_id="member-key" if member else None,
                    request_id=f"request-{member}",
                    model="model-alpha",
                    status="success",
                    cost_usd=cost,
                    requested_at=now,
                )
                for member, cost in ((True, 20), (False, 80))
            ]
        )
        await session.commit()
        reset_pool_share_cache()
        windows = await pool_share_windows(session, "member", 50, now=now)
        assert len(windows) == 1
        assert windows[0].used_percent == pytest.approx(16)
        true_global_share = 80 * 20 / (20 + 80 + remote_extra_cost)
        assert windows[0].used_percent >= true_global_share
    reset_pool_share_cache()
