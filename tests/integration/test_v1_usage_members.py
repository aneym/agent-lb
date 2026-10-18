from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.utils.time import utcnow
from app.modules.team.service import reset_team_usage_cache
from app.modules.team.windows import window_end, window_start
from tests.integration.usage_api_helpers import _create_api_key as _create_api_key
from tests.integration.usage_api_helpers import _create_member_key as _create_member_key
from tests.integration.usage_api_helpers import _member_key_id as _member_key_id
from tests.integration.usage_api_helpers import _seed_member_request_log as _seed_member_request_log
from tests.integration.usage_api_helpers import _window as _window

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_v1_usage_member_is_null_for_a_key_without_a_member(async_client, db_setup):
    _, plain_key = await _create_api_key(name="no-member")

    response = await async_client.get("/v1/usage", headers={"Authorization": f"Bearer {plain_key}"})

    assert response.status_code == 200
    assert response.json()["member"] is None


@pytest.mark.asyncio
async def test_v1_usage_member_reports_caps_usage_and_window_boundaries(async_client, db_setup):
    member, plain_key = await _create_member_key(
        async_client,
        costCapDayUsd=10.0,
        costCapWeekUsd=40.0,
        costCapMonthUsd=100.0,
        tokenCapDay=1_000,
        allowedModels=["model-alpha"],
    )

    now = utcnow()
    key_id = await _member_key_id(member["id"])
    await _seed_member_request_log(api_key_id=key_id, requested_at=now, cost_usd=2.0, tokens=200)

    response = await async_client.get("/v1/usage", headers={"Authorization": f"Bearer {plain_key}"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["member"]["id"] == member["id"]
    assert payload["member"]["name"] == member["name"]
    assert payload["member"]["status"] == "active"
    assert payload["member"]["gate"] == "ok"
    assert payload["member"]["allowed_models"] == ["model-alpha"]
    assert [entry["window"] for entry in payload["member"]["windows"]] == ["day", "week", "month"]

    day = _window(payload, "day")
    assert day["cost_cap_usd"] == 10.0
    assert day["token_cap"] == 1_000
    assert day["cost_usd"] == pytest.approx(2.0)
    assert day["tokens"] == 200
    assert day["window_start"] == window_start("day", now).isoformat() + "Z"
    assert day["window_end"] == window_end("day", now).isoformat() + "Z"

    week = _window(payload, "week")
    assert week["cost_cap_usd"] == 40.0
    assert week["token_cap"] is None
    assert week["window_start"] == window_start("week", now).isoformat() + "Z"

    month = _window(payload, "month")
    assert month["cost_cap_usd"] == 100.0
    assert month["window_end"] == window_end("month", now).isoformat() + "Z"


@pytest.mark.asyncio
async def test_v1_usage_member_gate_tracks_near_cap_and_over_cap(async_client, db_setup):
    member, plain_key = await _create_member_key(async_client, costCapDayUsd=10.0)
    key_id = await _member_key_id(member["id"])

    now = utcnow()
    await _seed_member_request_log(api_key_id=key_id, requested_at=now, cost_usd=8.5, tokens=10)
    near = await async_client.get("/v1/usage", headers={"Authorization": f"Bearer {plain_key}"})
    assert near.json()["member"]["gate"] == "near_cap"

    await _seed_member_request_log(
        api_key_id=key_id,
        requested_at=now - timedelta(seconds=1),
        cost_usd=2.0,
        tokens=10,
    )
    over = await async_client.get("/v1/usage", headers={"Authorization": f"Bearer {plain_key}"})
    assert over.json()["member"]["gate"] == "over_cap"


@pytest.mark.asyncio
async def test_v1_usage_member_reports_suspension_without_blocking_the_call(async_client, db_setup):
    member, plain_key = await _create_member_key(async_client)

    suspended = await async_client.patch(f"/api/team/members/{member['id']}", json={"status": "suspended"})
    assert suspended.status_code == 200, suspended.text
    reset_team_usage_cache()

    response = await async_client.get("/v1/usage", headers={"Authorization": f"Bearer {plain_key}"})

    assert response.status_code == 200
    assert response.json()["member"]["status"] == "suspended"
    assert response.json()["member"]["gate"] == "suspended"


@pytest.mark.asyncio
async def test_v1_usage_member_caps_are_null_when_unset(async_client, db_setup):
    _, plain_key = await _create_member_key(async_client)

    response = await async_client.get("/v1/usage", headers={"Authorization": f"Bearer {plain_key}"})

    assert response.status_code == 200
    payload = response.json()
    for entry in payload["member"]["windows"]:
        assert entry["cost_cap_usd"] is None
        assert entry["token_cap"] is None
        assert entry["cost_usd"] == 0.0
        assert entry["tokens"] == 0
    assert payload["member"]["gate"] == "ok"
