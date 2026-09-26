from __future__ import annotations

from datetime import timedelta, timezone

import pytest

from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, ApiKey, RequestLog, TeamMember, UsageHistory
from app.db.session import SessionLocal
from app.modules.team.pool_share import pool_share_windows, reset_pool_share_cache

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "priced_member", "priced_other", "unpriced_member_tokens",
        "unpriced_other_tokens", "remote_extra", "expected_local",
    ),
    [
        (20, 80, 0, 0, 100, 8),  # priced rows only; two eligible accounts
        (20, 80, 20, 80, 100, 8),  # unpriced rows imputed at local priced cost/token
        (None, None, 20, 80, 100, 8),  # no priced rows: token fallback
        (20, 40, 0, 0, 0, 80 / 6),
    ],
)
async def test_local_member_share_never_understates_when_all_member_traffic_is_local(
    db_setup, priced_member, priced_other, unpriced_member_tokens, unpriced_other_tokens, remote_extra, expected_local
):
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
        session.add_all(
            [
                RequestLog(
                    account_id="a",
                    api_key_id="member-key" if member else None,
                    request_id=f"request-{member}-{kind}",
                    model="model-alpha",
                    status="success",
                    cost_usd=cost,
                    input_tokens=tokens,
                    output_tokens=0,
                    requested_at=now,
                )
                for member, kind, cost, tokens in (
                    (True, "priced", priced_member, 20 if priced_member is not None else unpriced_member_tokens),
                    (False, "priced", priced_other, 80 if priced_other is not None else unpriced_other_tokens),
                    (True, "unpriced", None, unpriced_member_tokens if priced_member is not None else 0),
                    (False, "unpriced", None, unpriced_other_tokens if priced_other is not None else 0),
                )
            ]
        )
        await session.commit()
        reset_pool_share_cache()
        windows = await pool_share_windows(session, "member", 50, now=now)
        assert len(windows) == 1
        assert windows[0].used_percent == pytest.approx(expected_local)
        # The global snapshot includes remote cost, while every member row is local.
        # Removing the remote portion only shrinks the observed denominator.
        member_units = priced_member if priced_member is not None else unpriced_member_tokens
        other_units = priced_other if priced_other is not None else unpriced_other_tokens
        true_global_share = 80 * member_units / (member_units + other_units + remote_extra) / 2
        assert windows[0].used_percent + 1e-9 >= true_global_share
    reset_pool_share_cache()
