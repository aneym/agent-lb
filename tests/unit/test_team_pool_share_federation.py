from __future__ import annotations

from datetime import timedelta, timezone

import pytest

from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, ApiKey, RequestLog, TeamMember, UsageHistory
from app.db.session import SessionLocal
from app.modules.team.pool_share import pool_share_windows, reset_pool_share_cache

pytestmark = pytest.mark.unit


# Each tuple is (priced cost, priced tokens, unpriced tokens). Remote rows
# belong to someone else; all member traffic stays on this LB instance.
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("member", "other", "remote"),
    [
        ((20, 20, 0), (80, 80, 0), (100, 100, 0)),
        ((20, 20, 20), (80, 80, 80), (0.01, 1_000_000, 0)),
        ((10, 10, 0), (0, 0, 1_000), (1, 1_000_000, 0)),
        ((0, 0, 20), (0, 0, 80), (1, 1_000_000, 0)),
        ((20, 20, 0), (40, 80, 0), (0, 0, 1_000)),
        ((20, 20, 20), (40, 80, 80), (0, 0, 1_000)),
        ((0, 0, 20), (0, 0, 80), (0, 0, 100)),
    ],
)
async def test_local_member_share_never_understates_when_all_member_traffic_is_local(db_setup, member, other, remote):
    del db_setup
    now = utcnow().replace(microsecond=0)
    reset = now + timedelta(days=3)
    async with SessionLocal() as session:
        session.add(TeamMember(id="member", name="Member", pool_share_percent=50))
        for account_id, used in (("a", 80), ("b", 40)):
            session.add(
                Account(
                    id=account_id,
                    email=f"{account_id}@example.invalid",
                    plan_type="plus",
                    access_token_encrypted=b"a",
                    refresh_token_encrypted=b"b",
                    last_refresh=now,
                    status=AccountStatus.ACTIVE,
                )
            )
            session.add(
                UsageHistory(
                    account_id=account_id,
                    window="primary",
                    used_percent=used,
                    window_minutes=10080,
                    reset_at=int(reset.replace(tzinfo=timezone.utc).timestamp()),
                    recorded_at=now,
                )
            )
        await session.flush()
        session.add(ApiKey(id="member-key", name="Member", member_id="member", key_hash="hash", key_prefix="sk-clb"))
        for label, (cost, priced_tokens, unpriced_tokens), key in (
            ("member", member, "member-key"),
            ("other", other, None),
        ):
            for kind, value, tokens in (("priced", cost, priced_tokens), ("unpriced", None, unpriced_tokens)):
                if not tokens and value is None:
                    continue
                session.add(
                    RequestLog(
                        account_id="a",
                        api_key_id=key,
                        request_id=f"{label}-{kind}",
                        model="model-alpha",
                        status="success",
                        cost_usd=value,
                        input_tokens=tokens,
                        output_tokens=0,
                        requested_at=now,
                    )
                )
        await session.commit()
        reset_pool_share_cache()
        local_share = (await pool_share_windows(session, "member", 50, now=now))[0].used_percent

        # Independent global oracle: apply the spec's imputation formula to
        # the local rows plus the remote instance's priced/unpriced rows.
        priced_cost = member[0] + other[0] + remote[0]
        priced_tokens = member[1] + other[1] + remote[1]
        unpriced_tokens = member[2] + other[2] + remote[2]
        if priced_tokens:
            rate = priced_cost / priced_tokens
            member_cost = member[0] + member[2] * rate
            total_cost = priced_cost + unpriced_tokens * rate
        else:
            member_cost = member[1] + member[2]
            total_cost = priced_tokens + unpriced_tokens
        global_share = 80 * member_cost / total_cost / 2 if total_cost else 0
        assert local_share + 1e-9 >= global_share
        if member == (10, 10, 0) and other == (0, 0, 1_000):
            assert global_share > 30  # A naive local-rate estimate would be below 1%.
    reset_pool_share_cache()
