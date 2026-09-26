from __future__ import annotations

from datetime import timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from starlette.testclient import WebSocketDenialResponse

from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, ApiKey, ApiKeyAccountAssignment, RequestLog, UsageHistory
from app.db.session import SessionLocal
from app.modules.team.pool_share import pool_share_windows, reset_pool_share_cache
from app.modules.team.service import reset_team_usage_cache

pytestmark = pytest.mark.integration


async def _seed_account(session, account_id, now, *, used=40, length=10080, reset=None, window="primary"):
    reset = reset if reset is not None else now + timedelta(days=3)
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
    await session.flush()
    session.add(
        UsageHistory(
            account_id=account_id,
            window=window,
            used_percent=used,
            window_minutes=length,
            reset_at=int(reset.replace(tzinfo=timezone.utc).timestamp()),
            recorded_at=now - timedelta(hours=1),
        )
    )


def _log(account, key, now, cost, tokens):
    return RequestLog(
        account_id=account,
        api_key_id=key,
        request_id=f"{account}-{key}-{now}-{cost}-{tokens}",
        model="model-alpha",
        status="success",
        cost_usd=cost,
        input_tokens=tokens,
        output_tokens=0,
        requested_at=now,
    )


@pytest.mark.asyncio
async def test_pool_share_cost_math_imputation_rolled_and_cache(db_setup, monkeypatch):
    del db_setup
    import app.modules.team.pool_share as pool_module

    now = utcnow().replace(microsecond=0)
    clock = [1000.0]
    monkeypatch.setattr(pool_module.time, "monotonic", lambda: clock[0])
    reset_pool_share_cache()
    async with SessionLocal() as session:
        await _seed_account(session, "a", now)
        await _seed_account(session, "b", now, used=99)
        await _seed_account(session, "legacy", now, reset=now - timedelta(days=80), window="secondary")
        session.add(ApiKey(id="key", name="member", member_id=None, key_hash="keyhash", key_prefix="sk-clb-key"))
        await session.flush()
        from app.db.models import TeamMember

        session.add(TeamMember(id="member", name="Ada", pool_share_percent=6))
        await session.flush()
        (await session.execute(select(ApiKey).where(ApiKey.id == "key"))).scalar_one().member_id = "member"
        session.add_all([_log("a", "key", now, 25, 100), _log("a", None, now, 75, 300)])
        await session.commit()
        windows = await pool_share_windows(session, "member", 6, now=now)
        assert len(windows) == 1
        assert windows[0].window == "pool_week"
        assert windows[0].used_percent == pytest.approx(5)
        assert windows[0].reset_at == now + timedelta(days=3)

        session.add(_log("a", "key", now + timedelta(seconds=1), None, 400))
        await session.commit()
        assert (await pool_share_windows(session, "member", 6, now=now))[0].used_percent == pytest.approx(5)
        clock[0] += 5.1
        assert (await pool_share_windows(session, "member", 6, now=now))[0].used_percent == pytest.approx(12.5)

        session.add(_log("a", "key", now + timedelta(seconds=2), None, 100))
        await session.commit()
        reset_pool_share_cache()
        assert (await pool_share_windows(session, "member", 6, now=now))[0].used_percent > 12.5
        session.add(
            ApiKey(id="spare", name="other", member_id="member", key_hash="sparehash", key_prefix="sk-clb-spare")
        )
        await session.execute(ApiKey.__table__.update().where(ApiKey.id == "key").values(member_id=None))
        await session.commit()
        reset_pool_share_cache()
        assert (await pool_share_windows(session, "member", 6, now=now))[0].used_percent == 0
    reset_pool_share_cache()


@pytest.mark.asyncio
async def test_pool_share_scoped_reachability_rolled_and_token_fallback(db_setup):
    del db_setup
    now = utcnow().replace(microsecond=0)
    async with SessionLocal() as session:
        from app.db.models import TeamMember

        session.add(TeamMember(id="member", name="Ada", pool_share_percent=30))
        await session.flush()
        session.add(
            ApiKey(
                id="key",
                name="member",
                member_id="member",
                key_hash="keyhash",
                key_prefix="sk-clb-key",
                account_assignment_scope_enabled=True,
            )
        )
        await _seed_account(session, "a", now, used=120)
        await _seed_account(session, "b", now, used=100, reset=now - timedelta(minutes=2))
        await session.flush()
        session.add(ApiKeyAccountAssignment(api_key_id="key", account_id="a"))
        session.add_all([_log("a", "key", now, None, 10), _log("a", None, now, None, 30)])
        await session.commit()
        assert (await pool_share_windows(session, "member", 30, now=now))[0].used_percent == pytest.approx(25)
        session.add(ApiKeyAccountAssignment(api_key_id="key", account_id="b"))
        await session.commit()
        reset_pool_share_cache()
        window = (await pool_share_windows(session, "member", 30, now=now))[0]
        assert window.used_percent == pytest.approx(12.5)
        assert window.reset_at == now + timedelta(days=3)
        await session.execute(
            ApiKey.__table__.update().where(ApiKey.id == "key").values(account_assignment_scope_enabled=False)
        )
        await session.commit()
        reset_pool_share_cache()
        assert (await pool_share_windows(session, "member", 30, now=now))[0].used_percent == pytest.approx(12.5)
    reset_pool_share_cache()


@pytest.mark.asyncio
async def test_pool_share_api_usage_and_websocket_handshake(async_client, app_instance):
    now = utcnow().replace(microsecond=0)
    created = await async_client.post("/api/team/members", json={"name": "Ada", "poolSharePercent": 10})
    assert created.status_code == 200, created.text
    member = created.json()
    assert (member["poolSharePercent"], member["poolShare"], member["poolShareKnown"]) == (10, [], False)
    member_id = member["id"]
    for invalid in (0, -1, 100.001):
        rejected = await async_client.post(
            "/api/team/members", json={"name": f"Rejected-{invalid}", "poolSharePercent": invalid}
        )
        assert rejected.status_code == 422
    for invalid in (0, -1, 100.001):
        response = await async_client.patch(f"/api/team/members/{member_id}", json={"poolSharePercent": invalid})
        assert response.status_code == 422
    key = (await async_client.post(f"/api/team/members/{member_id}/keys", json={})).json()
    async with SessionLocal() as session:
        await _seed_account(session, "a", now, used=80, length=300, reset=now + timedelta(hours=2))
        session.add(_log("a", key["id"], now, 10, 100))
        await session.commit()
    reset_pool_share_cache()
    reset_team_usage_cache()
    listed = (await async_client.get("/api/team/members")).json()[0]
    assert listed["gate"] == "over_cap"
    assert listed["poolShareKnown"] is True
    assert listed["poolShare"][0]["window"] == "pool_5h"
    assert listed["poolShare"][0]["usedPercent"] == pytest.approx(80)
    assert listed["poolShare"][0]["limitPercent"] == 10
    headers = {"Authorization": f"Bearer {key['key']}"}
    usage = await async_client.get("/v1/usage", headers=headers)
    assert usage.status_code == 200, usage.text
    assert usage.json()["member"]["poolSharePercent"] == 10
    assert usage.json()["member"]["poolWindows"][0]["usedPercent"] == pytest.approx(80)
    assert (await async_client.put("/api/settings", json={"teamModeEnabled": True})).status_code == 200
    for route in ("/backend-api/codex/responses", "/v1/responses"):
        with TestClient(app_instance, client=("127.0.0.1", 50000)) as client:
            with pytest.raises(WebSocketDenialResponse) as excinfo:
                with client.websocket_connect(route, headers=headers):
                    pass
            assert excinfo.value.status_code == 429, excinfo.value.text
            assert excinfo.value.json()["error"]["type"] == "team_member_over_cap"
            assert excinfo.value.headers["x-team-window"] == "pool_5h"
            assert excinfo.value.headers["x-team-reset"].endswith("Z")
    near = await async_client.patch(f"/api/team/members/{member_id}", json={"poolSharePercent": 100})
    assert near.status_code == 200
    assert near.json()["gate"] == "near_cap"
    assert near.json()["poolShare"][0]["limitPercent"] == 100
    cleared = await async_client.patch(f"/api/team/members/{member_id}", json={"poolSharePercent": None})
    assert cleared.status_code == 200
    assert cleared.json()["poolSharePercent"] is None
    assert cleared.json()["poolShare"] == []


@pytest.mark.asyncio
async def test_pool_share_zero_usage_and_unpriced_tokens(db_setup):
    del db_setup
    now = utcnow().replace(microsecond=0)
    from app.db.models import TeamMember

    async with SessionLocal() as session:
        session.add(TeamMember(id="member", name="Ada", pool_share_percent=25))
        await session.flush()
        session.add(ApiKey(id="key", name="member", member_id="member", key_hash="keyhash", key_prefix="sk-clb-key"))
        await _seed_account(session, "a", now, used=50, length=240, reset=now + timedelta(hours=2))
        await session.commit()
        assert (await pool_share_windows(session, "member", 25, now=now))[0].used_percent == 0
        session.add_all([_log("a", "key", now, None, 50), _log("a", None, now, None, 50)])
        await session.commit()
        reset_pool_share_cache()
        window = (await pool_share_windows(session, "member", 25, now=now))[0]
        assert window.window == "pool_240m"
        assert window.used_percent == pytest.approx(25)
    reset_pool_share_cache()
