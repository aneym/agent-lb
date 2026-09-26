from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.utils.time import utcnow
from app.db.models import ApiKey, RequestLog
from app.db.session import SessionLocal
from app.modules.team.windows import window_start

pytestmark = pytest.mark.integration


async def _create_member(async_client, **overrides) -> dict:
    payload = {"name": "Ada"}
    payload.update(overrides)
    response = await async_client.post("/api/team/members", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


async def _issue_key(async_client, member_id: str, **overrides) -> dict:
    response = await async_client.post(f"/api/team/members/{member_id}/keys", json=dict(overrides))
    assert response.status_code == 200, response.text
    return response.json()


async def _seed_request_log(
    *,
    api_key_id: str,
    created_at,
    model: str = "model-alpha",
    cost_usd: float = 1.0,
    input_tokens: int = 100,
    output_tokens: int = 50,
) -> None:
    async with SessionLocal() as session:
        session.add(
            RequestLog(
                api_key_id=api_key_id,
                request_id=f"req-{api_key_id}-{created_at.isoformat()}-{model}",
                model=model,
                status="success",
                cost_usd=cost_usd,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                requested_at=created_at,
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_team_member_crud(async_client, db_setup):
    created = await _create_member(
        async_client,
        email="ada@example.com",
        costCapDayUsd=5.0,
        tokenCapWeek=1_000_000,
        allowedModels=["model-alpha"],
        notes="pairs on the compiler",
    )
    assert created["status"] == "active"
    assert created["gate"] == "ok"
    assert created["costCapDayUsd"] == 5.0
    assert created["tokenCapWeek"] == 1_000_000
    assert created["allowedModels"] == ["model-alpha"]
    assert created["usage"] == {
        "day": {"costUsd": 0.0, "tokens": 0},
        "week": {"costUsd": 0.0, "tokens": 0},
        "month": {"costUsd": 0.0, "tokens": 0},
    }
    assert created["keys"] == []

    duplicate = await async_client.post("/api/team/members", json={"name": "Ada"})
    assert duplicate.status_code == 400
    assert duplicate.json()["error"]["code"] == "invalid_team_member_payload"

    listed = await async_client.get("/api/team/members")
    assert listed.status_code == 200
    assert [member["id"] for member in listed.json()] == [created["id"]]

    patched = await async_client.patch(
        f"/api/team/members/{created['id']}",
        json={"status": "suspended", "notes": None, "costCapDayUsd": 9.5},
    )
    assert patched.status_code == 200
    body = patched.json()
    assert body["status"] == "suspended"
    assert body["gate"] == "suspended"
    assert body["notes"] is None
    assert body["costCapDayUsd"] == 9.5

    missing = await async_client.patch("/api/team/members/does-not-exist", json={"status": "active"})
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_issue_key_attaches_member_and_delete_detaches(async_client, db_setup):
    member = await _create_member(async_client, name="Grace")

    issued = await _issue_key(async_client, member["id"], name="grace-laptop")
    assert issued["key"].startswith("sk-clb-")
    assert issued["name"] == "grace-laptop"

    async with SessionLocal() as session:
        row = (await session.execute(select(ApiKey).where(ApiKey.id == issued["id"]))).scalar_one()
        assert row.member_id == member["id"]

    detail = await async_client.get("/api/team/members")
    keys = detail.json()[0]["keys"]
    assert [key["id"] for key in keys] == [issued["id"]]
    assert keys[0]["keyPrefix"] == issued["keyPrefix"]
    assert keys[0]["isActive"] is True

    deleted = await async_client.delete(f"/api/team/members/{member['id']}")
    assert deleted.status_code == 204

    async with SessionLocal() as session:
        row = (await session.execute(select(ApiKey).where(ApiKey.id == issued["id"]))).scalar_one()
        assert row.member_id is None
        assert row.is_active is True

    assert (await async_client.get("/api/team/members")).json() == []


@pytest.mark.asyncio
async def test_issue_key_without_payload_uses_member_name(async_client, db_setup):
    member = await _create_member(async_client, name="Katherine")
    response = await async_client.post(f"/api/team/members/{member['id']}/keys")
    assert response.status_code == 200, response.text
    assert response.json()["name"] == "Katherine"


@pytest.mark.asyncio
async def test_usage_windows_aggregate_seeded_request_logs(async_client, db_setup):
    member = await _create_member(async_client, name="Barbara", costCapDayUsd=100.0)
    issued = await _issue_key(async_client, member["id"])

    now = utcnow()
    day_start = window_start("day", now)
    month_start = window_start("month", now)

    # Inside today's window.
    await _seed_request_log(api_key_id=issued["id"], created_at=day_start + timedelta(minutes=1), cost_usd=1.5)
    # Before today, but inside this month (and skipped by the day window).
    before_today = day_start - timedelta(hours=1)
    in_month = before_today >= month_start
    await _seed_request_log(
        api_key_id=issued["id"],
        created_at=before_today,
        model="model-beta",
        cost_usd=2.0,
        input_tokens=10,
        output_tokens=5,
    )
    # A log for a key that belongs to nobody must never count.
    await _seed_request_log(api_key_id="unrelated-key", created_at=day_start + timedelta(minutes=2), cost_usd=99.0)

    listed = (await async_client.get("/api/team/members")).json()[0]
    assert listed["usage"]["day"] == {"costUsd": 1.5, "tokens": 150}
    assert listed["gate"] == "ok"
    if in_month:
        assert listed["usage"]["month"] == {"costUsd": 3.5, "tokens": 165}

    day_usage = await async_client.get(f"/api/team/members/{member['id']}/usage", params={"window": "day"})
    assert day_usage.status_code == 200
    body = day_usage.json()
    assert body["window"] == "day"
    assert body["totals"] == {"costUsd": 1.5, "tokens": 150}
    assert [row["model"] for row in body["models"]] == ["model-alpha"]
    assert body["models"][0]["requests"] == 1
    assert len(body["series"]) == 1
    assert body["series"][0]["costUsd"] == 1.5

    month_usage = await async_client.get(f"/api/team/members/{member['id']}/usage", params={"window": "month"})
    assert month_usage.status_code == 200
    if in_month:
        assert {row["model"] for row in month_usage.json()["models"]} == {"model-alpha", "model-beta"}

    bad_window = await async_client.get(f"/api/team/members/{member['id']}/usage", params={"window": "year"})
    assert bad_window.status_code == 422


@pytest.mark.asyncio
async def test_gate_chip_reflects_near_and_over_cap(async_client, db_setup):
    member = await _create_member(async_client, name="Margaret", costCapDayUsd=10.0)
    issued = await _issue_key(async_client, member["id"])
    day_start = window_start("day", utcnow())

    await _seed_request_log(api_key_id=issued["id"], created_at=day_start + timedelta(minutes=1), cost_usd=8.5)
    assert (await async_client.get("/api/team/members")).json()[0]["gate"] == "near_cap"

    await _seed_request_log(
        api_key_id=issued["id"],
        created_at=day_start + timedelta(minutes=2),
        model="model-beta",
        cost_usd=2.0,
    )
    assert (await async_client.get("/api/team/members")).json()[0]["gate"] == "over_cap"


@pytest.mark.asyncio
async def test_onboarding_payload_uses_configured_base_url(async_client, db_setup):
    member = await _create_member(async_client, name="Dorothy")

    default_payload = (await async_client.get(f"/api/team/members/{member['id']}/onboarding")).json()
    assert default_payload["baseUrl"] == "http://testserver"

    updated = await async_client.put(
        "/api/settings",
        json={"teamModeEnabled": True, "teamPublicBaseUrl": "https://lb.example.com/"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["teamModeEnabled"] is True
    assert updated.json()["teamPublicBaseUrl"] == "https://lb.example.com/"

    response = await async_client.get(f"/api/team/members/{member['id']}/onboarding")
    assert response.status_code == 200
    payload = response.json()
    assert payload["baseUrl"] == "https://lb.example.com"

    zsh = payload["snippets"]["macosZsh"]
    powershell = payload["snippets"]["windowsPowershell"]
    assert "export ANTHROPIC_BASE_URL='https://lb.example.com'" in zsh
    assert 'export ANTHROPIC_AUTH_TOKEN="<key>"' in zsh
    assert "export OPENAI_BASE_URL='https://lb.example.com/v1'" in zsh
    assert 'export OPENAI_API_KEY="<key>"' in zsh
    assert "$env:ANTHROPIC_BASE_URL = 'https://lb.example.com'" in powershell
    assert "$env:OPENAI_BASE_URL = 'https://lb.example.com/v1'" in powershell
    assert "sk-clb-" not in zsh and "sk-clb-" not in powershell

    missing = await async_client.get("/api/team/members/does-not-exist/onboarding")
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_calendar_usage_across_keys_at_exact_window_boundaries(async_client, db_setup, monkeypatch):
    import app.modules.team.service as team_service

    now = datetime(2026, 9, 23, 12)
    monkeypatch.setattr(team_service, "utcnow", lambda: now)
    member = await _create_member(async_client, name="Calendar")
    keys = [await _issue_key(async_client, member["id"], name=name) for name in ("laptop", "desktop")]
    rows = [
        (datetime(2026, 8, 31, 23, 59, 59), 100.0),
        (datetime(2026, 9, 1), 1.0),
        (datetime(2026, 9, 20, 23, 59, 59), 2.0),
        (datetime(2026, 9, 21), 4.0),
        (datetime(2026, 9, 22, 23, 59, 59), 8.0),
        (datetime(2026, 9, 23), 16.0),
    ]
    for index, (at, cost) in enumerate(rows):
        await _seed_request_log(
            api_key_id=keys[index % 2]["id"], created_at=at, cost_usd=cost, input_tokens=10, output_tokens=5
        )
    for window, cost, tokens, days in (("day", 16.0, 15, 1), ("week", 28.0, 45, 3), ("month", 31.0, 75, 5)):
        response = await async_client.get(f"/api/team/members/{member['id']}/usage", params={"window": window})
        assert response.status_code == 200
        usage = response.json()
        assert usage["totals"] == {"costUsd": cost, "tokens": tokens}
        assert len(usage["series"]) == days
        assert sum(row["costUsd"] for row in usage["series"]) == cost
        assert sum(row["tokens"] for row in usage["models"]) == tokens


@pytest.mark.asyncio
async def test_reassigning_key_invalidates_cached_member_identity(async_client, db_setup):
    from app.core.auth.dependencies import _validate_api_key_token
    from app.modules.api_keys.repository import ApiKeysRepository
    from app.modules.api_keys.service import ApiKeysService, ApiKeyUpdateData

    original = await _create_member(async_client, name="Original")
    replacement = await _create_member(async_client, name="Replacement")
    issued = await _issue_key(async_client, original["id"])
    cached = await _validate_api_key_token(issued["key"])
    assert cached.member_id == original["id"]

    async with SessionLocal() as session:
        await ApiKeysService(ApiKeysRepository(session)).update_key(
            issued["id"], ApiKeyUpdateData(member_id=replacement["id"], member_id_set=True)
        )
    refreshed = await _validate_api_key_token(issued["key"])
    assert refreshed.member_id == replacement["id"]


@pytest.mark.asyncio
async def test_member_key_expiration_is_preserved_and_enforced(async_client, db_setup, monkeypatch):
    from app.core.auth import dependencies as auth
    from app.core.exceptions import ProxyAuthError

    member = await _create_member(async_client, name="Expiring")
    expires = utcnow().replace(microsecond=0) + timedelta(hours=1)
    issued = await _issue_key(async_client, member["id"], expiresAt=expires.isoformat() + "Z")
    assert (await auth._validate_api_key_token(issued["key"])).expires_at == expires
    after_expiry = expires + timedelta(microseconds=1)
    monkeypatch.setattr(auth, "utcnow", lambda: after_expiry)
    import app.modules.api_keys.service as api_keys_service

    monkeypatch.setattr(api_keys_service, "utcnow", lambda: after_expiry)
    with pytest.raises(ProxyAuthError):
        await auth._validate_api_key_token(issued["key"])
