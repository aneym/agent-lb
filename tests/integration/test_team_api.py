from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.utils.time import utcnow
from app.db.models import Account, ApiKey, ApiKeyAccountAssignment, RequestLog
from app.db.session import SessionLocal
from app.modules.team.windows import window_start

pytestmark = pytest.mark.integration


async def _create_member(async_client, **overrides) -> dict:
    payload = {"name": "Ada"}
    payload.update(overrides)
    response = await async_client.post("/api/team/members", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


async def _seed_account(account_id: str) -> str:
    async with SessionLocal() as session:
        session.add(
            Account(
                id=account_id,
                email=f"{account_id}@example.com",
                plan_type="plus",
                access_token_encrypted=b"a",
                refresh_token_encrypted=b"b",
                last_refresh=utcnow(),
            )
        )
        await session.commit()
    return account_id


async def _issue_key(async_client, member_id: str, **overrides) -> dict:
    account_id = await _seed_account(f"account-{member_id}")
    response = await async_client.post(
        f"/api/team/members/{member_id}/keys", json={"assignedAccountIds": [account_id], **overrides}
    )
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
async def test_issue_key_with_scope_defaults_to_member_name(async_client, db_setup):
    member = await _create_member(async_client, name="Member")
    account_id = await _seed_account("account-member-name")
    response = await async_client.post(
        f"/api/team/members/{member['id']}/keys", json={"assignedAccountIds": [account_id]}
    )
    assert response.status_code == 200, response.text
    assert response.json()["name"] == "Member"


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [None, {}, {"assignedAccountIds": []}])
async def test_issue_key_requires_account_scope(async_client, db_setup, payload):
    member = await _create_member(async_client, name="Second person")
    path = f"/api/team/members/{member['id']}/keys"
    response = await async_client.post(path) if payload is None else await async_client.post(path, json=payload)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "member_key_scope_required"

    async with SessionLocal() as session:
        rows = (await session.execute(select(ApiKey).where(ApiKey.member_id == member["id"]))).scalars().all()
        assert rows == []


@pytest.mark.asyncio
async def test_issue_key_with_account_scope_persists_assignments(async_client, db_setup):
    member = await _create_member(async_client, name="Second person")
    account_id = await _seed_account("account-assigned")
    response = await async_client.post(
        f"/api/team/members/{member['id']}/keys", json={"assignedAccountIds": [account_id]}
    )
    assert response.status_code == 200, response.text
    issued = response.json()
    assert issued["accountAssignmentScopeEnabled"] is True
    assert issued["assignedAccountIds"] == [account_id]

    async with SessionLocal() as session:
        row = (await session.execute(select(ApiKey).where(ApiKey.id == issued["id"]))).scalar_one()
        assert row.member_id == member["id"]
        assert row.account_assignment_scope_enabled is True
        assignments = (
            (
                await session.execute(
                    select(ApiKeyAccountAssignment.account_id).where(ApiKeyAccountAssignment.api_key_id == row.id)
                )
            )
            .scalars()
            .all()
        )
        assert assignments == [account_id]


@pytest.mark.asyncio
async def test_issue_key_rejects_unknown_account_without_persisting_key(async_client, db_setup):
    member = await _create_member(async_client, name="Second person")
    response = await async_client.post(
        f"/api/team/members/{member['id']}/keys", json={"assignedAccountIds": ["missing-account"]}
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_api_key_payload"

    async with SessionLocal() as session:
        rows = (await session.execute(select(ApiKey).where(ApiKey.member_id == member["id"]))).scalars().all()
        assert rows == []


@pytest.mark.asyncio
async def test_member_key_cannot_clear_assignments_but_can_change_name(async_client, db_setup):
    member = await _create_member(async_client, name="Second person")
    account_id = await _seed_account("account-to-keep")
    issued = (
        await async_client.post(f"/api/team/members/{member['id']}/keys", json={"assignedAccountIds": [account_id]})
    ).json()
    key_id = issued["id"]

    rejected = await async_client.patch(f"/api/api-keys/{key_id}", json={"assignedAccountIds": []})
    assert rejected.status_code == 400
    assert rejected.json()["error"]["code"] == "member_key_scope_required"

    async with SessionLocal() as session:
        row = (await session.execute(select(ApiKey).where(ApiKey.id == key_id))).scalar_one()
        assert row.account_assignment_scope_enabled is True
        assignments = (
            (
                await session.execute(
                    select(ApiKeyAccountAssignment.account_id).where(ApiKeyAccountAssignment.api_key_id == key_id)
                )
            )
            .scalars()
            .all()
        )
        assert assignments == [account_id]

    renamed = await async_client.patch(f"/api/api-keys/{key_id}", json={"name": "Second person's laptop"})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "Second person's laptop"
    assert renamed.json()["assignedAccountIds"] == [account_id]


@pytest.mark.asyncio
async def test_non_member_key_can_clear_assignments(async_client, db_setup):
    account_id = await _seed_account("account-non-member")
    created = await async_client.post("/api/api-keys/", json={"name": "Owner key", "assignedAccountIds": [account_id]})
    assert created.status_code == 200, created.text

    cleared = await async_client.patch(f"/api/api-keys/{created.json()['id']}", json={"assignedAccountIds": []})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["accountAssignmentScopeEnabled"] is False
    assert cleared.json()["assignedAccountIds"] == []


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
