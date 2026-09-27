from __future__ import annotations

import json
from datetime import timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, event, select
from starlette.testclient import WebSocketDenialResponse

from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, ApiKey, ApiKeyAccountAssignment, RequestLog, UsageHistory
from app.db.session import SessionLocal, engine
from app.modules.team.pool_share import pool_share_windows, reset_pool_share_cache
from app.modules.team.service import invalidate_team_member_caches, reset_team_usage_cache

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
        assert windows[0].used_percent == pytest.approx(40 * 0.25 / 3)
        assert windows[0].reset_at == now + timedelta(days=3)

        session.add(_log("a", "key", now + timedelta(seconds=1), None, 400))
        await session.commit()
        assert (await pool_share_windows(session, "member", 6, now=now))[0].used_percent == pytest.approx(40 * 0.25 / 3)
        clock[0] += 5.1
        assert (await pool_share_windows(session, "member", 6, now=now))[0].used_percent == pytest.approx(40 / 3)

        session.add(_log("a", None, now + timedelta(seconds=2), None, 400))
        await session.commit()
        reset_pool_share_cache()
        assert (await pool_share_windows(session, "member", 6, now=now))[0].used_percent == pytest.approx(20 / 3)
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
async def test_pool_share_window_start_and_invalidation(db_setup):
    del db_setup
    now = utcnow().replace(microsecond=0)
    from app.db.models import TeamMember

    async with SessionLocal() as session:
        session.add(TeamMember(id="member", name="Ada", pool_share_percent=20))
        await _seed_account(session, "a", now, used=60, length=300, reset=now + timedelta(hours=1))
        await session.flush()
        session.add(ApiKey(id="key", name="member", member_id="member", key_hash="hash", key_prefix="sk-clb-key"))
        # This row falls just before the new window; it must not dilute the member's share.
        session.add(_log("a", None, now - timedelta(hours=4, seconds=1), 90, 0))
        session.add(_log("a", "key", now, 10, 0))
        await session.commit()
        queries = []

        def count_queries(conn, cursor, statement, parameters, context, executemany):
            if statement.lstrip().upper().startswith(("SELECT", "WITH")):
                queries.append(statement)

        event.listen(engine.sync_engine, "before_cursor_execute", count_queries)
        try:
            first = await pool_share_windows(session, "member", 20, now=now)
            assert len(queries) <= 6
            assert first[0].used_percent == pytest.approx(60)
            queries.clear()
            assert (await pool_share_windows(session, "member", 20, now=now))[0].used_percent == pytest.approx(60)
            assert queries == []
            session.add(_log("a", None, now + timedelta(seconds=1), 10, 0))
            await session.commit()
            invalidate_team_member_caches()
            queries.clear()
            assert (await pool_share_windows(session, "member", 20, now=now))[0].used_percent == pytest.approx(30)
            assert len(queries) <= 6
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", count_queries)
    reset_pool_share_cache()


@pytest.mark.asyncio
async def test_pool_share_api_usage_and_websocket_handshake(async_client, app_instance):
    now = utcnow().replace(microsecond=0)
    created = await async_client.post("/api/team/members", json={"name": "Ada", "poolSharePercent": 10})
    assert created.status_code == 200, created.text
    member = created.json()
    assert (member["poolSharePercent"], member["poolShare"], member["poolShareKnown"]) == (10, [], False)
    member_id = member["id"]
    for invalid in (0, -1, 0.0004, 0.0011, 100.001):
        rejected = await async_client.post(
            "/api/team/members", json={"name": f"Rejected-{invalid}", "poolSharePercent": invalid}
        )
        assert rejected.status_code == 422
    for invalid in (0, -1, 0.0004, 0.0011, 100.001):
        response = await async_client.patch(f"/api/team/members/{member_id}", json={"poolSharePercent": invalid})
        assert response.status_code == 422
    async with SessionLocal() as session:
        await _seed_account(session, "a", now, used=80, length=300, reset=now + timedelta(hours=2))
        await session.commit()
    key = (await async_client.post(f"/api/team/members/{member_id}/keys", json={"assignedAccountIds": ["a"]})).json()
    async with SessionLocal() as session:
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
async def test_pool_share_existing_websocket_emits_over_cap_on_next_turn(async_client, app_instance):
    now = utcnow().replace(microsecond=0)
    member = (await async_client.post("/api/team/members", json={"name": "Ada", "poolSharePercent": 10})).json()
    async with SessionLocal() as session:
        await _seed_account(session, "a", now, used=80, length=300, reset=now + timedelta(hours=2))
        await session.execute(delete(RequestLog).where(RequestLog.account_id == "a"))
        await session.commit()
    key = (await async_client.post(f"/api/team/members/{member['id']}/keys", json={"assignedAccountIds": ["a"]})).json()
    assert (await async_client.put("/api/settings", json={"teamModeEnabled": True})).status_code == 200
    reset_pool_share_cache()
    for route in ("/backend-api/codex/responses", "/v1/responses"):
        with TestClient(app_instance, client=("127.0.0.1", 50000)) as client:
            with client.websocket_connect(route, headers={"Authorization": f"Bearer {key['key']}"}) as websocket:
                async with SessionLocal() as session:
                    session.add(_log("a", key["id"], now, 10, 100))
                    await session.commit()
                invalidate_team_member_caches()
                websocket.send_text(json.dumps({"type": "response.create", "model": "gpt-5.4", "input": "hello"}))
                error = json.loads(websocket.receive_text())
                assert error["type"] == "error"
                assert error["status"] == 429
                assert error["error"]["type"] == "team_member_over_cap"
        async with SessionLocal() as session:
            await session.execute(delete(RequestLog).where(RequestLog.account_id == "a"))
            await session.commit()
        reset_pool_share_cache()
    reset_pool_share_cache()


@pytest.mark.asyncio
async def test_pool_share_idle_rolled_window_has_future_advisory_reset_and_uses_cache(db_setup, monkeypatch):
    del db_setup
    import app.modules.team.pool_share as pool_module
    from app.db.models import TeamMember

    now = utcnow().replace(microsecond=0)
    clock = [1000.0]
    monkeypatch.setattr(pool_module.time, "monotonic", lambda: clock[0])
    async with SessionLocal() as session:
        session.add(TeamMember(id="member", name="Ada", pool_share_percent=25))
        await _seed_account(session, "rolled", now, used=90, length=300, reset=now - timedelta(minutes=1))
        await session.flush()
        session.add(ApiKey(id="idle-key", name="member", member_id="member", key_hash="idlehash", key_prefix="sk-clb"))
        await session.commit()
        reset_pool_share_cache()
        queries = []

        def count_queries(conn, cursor, statement, parameters, context, executemany):
            if statement.lstrip().upper().startswith(("SELECT", "WITH")):
                queries.append(statement)

        event.listen(engine.sync_engine, "before_cursor_execute", count_queries)
        try:
            first = (await pool_share_windows(session, "member", 25, now=now))[0]
            assert first.used_percent == 0
            assert first.reset_at > now
            queries.clear()
            assert (await pool_share_windows(session, "member", 25, now=now))[0] == first
            assert queries == []
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", count_queries)
    reset_pool_share_cache()


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
