from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.utils.time import utcnow
from app.db.models import (
    Account,
    AccountStatus,
    ApiKey,
    ApiKeyLimit,
    LimitType,
    LimitWindow,
    RequestLog,
    UsageHistory,
)
from app.db.session import SessionLocal
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.api_keys.service import ApiKeyCreateData, ApiKeysService, LimitRuleInput
from app.modules.request_logs.repository import RequestLogsRepository
from app.modules.team.service import reset_team_usage_cache
from app.modules.team.windows import window_end, window_start

pytestmark = pytest.mark.integration


async def _create_member_key(async_client, **member_overrides) -> tuple[dict, str]:
    """Create a team member plus one key attached to it; returns (member, plain key)."""

    payload = {"name": f"member-{utcnow().timestamp()}"}
    payload.update(member_overrides)
    created = await async_client.post("/api/team/members", json=payload)
    assert created.status_code == 200, created.text
    member = created.json()

    issued = await async_client.post(f"/api/team/members/{member['id']}/keys", json={})
    assert issued.status_code == 200, issued.text
    reset_team_usage_cache()
    return member, issued.json()["key"]


async def _member_key_id(member_id: str) -> str:
    async with SessionLocal() as session:
        result = await session.execute(select(ApiKey.id).where(ApiKey.member_id == member_id))
        return result.scalar_one()


async def _seed_member_request_log(*, api_key_id: str, requested_at, cost_usd: float, tokens: int) -> None:
    async with SessionLocal() as session:
        session.add(
            RequestLog(
                api_key_id=api_key_id,
                request_id=f"req-member-{api_key_id}-{requested_at.isoformat()}",
                model="model-alpha",
                status="success",
                cost_usd=cost_usd,
                input_tokens=tokens,
                output_tokens=0,
                requested_at=requested_at,
            )
        )
        await session.commit()
    reset_team_usage_cache()


def _window(payload: dict, name: str) -> dict:
    return next(entry for entry in payload["member"]["windows"] if entry["window"] == name)


async def _create_api_key(
    *,
    name: str,
    limits: list[LimitRuleInput] | None = None,
) -> tuple[str, str]:
    async with SessionLocal() as session:
        service = ApiKeysService(ApiKeysRepository(session))
        created = await service.create_key(
            ApiKeyCreateData(
                name=name,
                allowed_models=None,
                limits=limits or [],
            )
        )
    return created.id, created.key


async def _seed_upstream_usage(*, now) -> None:
    suffix = str(int(now.timestamp() * 1_000_000))
    account_a_id = f"acc-plus-a-{suffix}"
    account_b_id = f"acc-plus-b-{suffix}"

    async with SessionLocal() as session:
        session.add_all(
            [
                Account(
                    id=account_a_id,
                    chatgpt_account_id=f"chatgpt-plus-a-{suffix}",
                    email=f"plus-a-{suffix}@example.com",
                    plan_type="plus",
                    access_token_encrypted=b"a",
                    refresh_token_encrypted=b"b",
                    id_token_encrypted=b"c",
                    last_refresh=now,
                    status=AccountStatus.ACTIVE,
                    reset_at=None,
                ),
                Account(
                    id=account_b_id,
                    chatgpt_account_id=f"chatgpt-plus-b-{suffix}",
                    email=f"plus-b-{suffix}@example.com",
                    plan_type="plus",
                    access_token_encrypted=b"d",
                    refresh_token_encrypted=b"e",
                    id_token_encrypted=b"f",
                    last_refresh=now,
                    status=AccountStatus.ACTIVE,
                    reset_at=None,
                ),
                UsageHistory(
                    account_id=account_a_id,
                    recorded_at=now,
                    window="primary",
                    used_percent=10.0,
                    reset_at=int((now + timedelta(hours=4)).timestamp()),
                    window_minutes=300,
                ),
                UsageHistory(
                    account_id=account_b_id,
                    recorded_at=now,
                    window="primary",
                    used_percent=20.0,
                    reset_at=int((now + timedelta(hours=4)).timestamp()),
                    window_minutes=300,
                ),
                UsageHistory(
                    account_id=account_a_id,
                    recorded_at=now,
                    window="secondary",
                    used_percent=20.0,
                    reset_at=int((now + timedelta(days=6)).timestamp()),
                    window_minutes=10080,
                ),
                UsageHistory(
                    account_id=account_b_id,
                    recorded_at=now,
                    window="secondary",
                    used_percent=30.0,
                    reset_at=int((now + timedelta(days=6)).timestamp()),
                    window_minutes=10080,
                ),
            ]
        )
        await session.commit()


async def _seed_upstream_usage_partial(
    *,
    now,
    windows: tuple[str, ...],
    primary_reset_at: int | None | object = ...,
    secondary_reset_at: int | None | object = ...,
) -> None:
    suffix = str(int(now.timestamp() * 1_000_000))
    account_id = f"acc-plus-partial-{suffix}"

    entries: list[Account | UsageHistory] = [
        Account(
            id=account_id,
            chatgpt_account_id=f"chatgpt-plus-partial-{suffix}",
            email=f"plus-partial-{suffix}@example.com",
            plan_type="plus",
            access_token_encrypted=b"a",
            refresh_token_encrypted=b"b",
            id_token_encrypted=b"c",
            last_refresh=now,
            status=AccountStatus.ACTIVE,
            reset_at=None,
        )
    ]

    if "primary" in windows:
        if primary_reset_at is ...:
            primary_reset: int | None = int((now + timedelta(hours=4)).timestamp())
        else:
            primary_reset = primary_reset_at
        entries.append(
            UsageHistory(
                account_id=account_id,
                recorded_at=now,
                window="primary",
                used_percent=10.0,
                reset_at=primary_reset,
                window_minutes=300,
            )
        )
    if "secondary" in windows:
        if secondary_reset_at is ...:
            secondary_reset: int | None = int((now + timedelta(days=6)).timestamp())
        else:
            secondary_reset = secondary_reset_at
        entries.append(
            UsageHistory(
                account_id=account_id,
                recorded_at=now,
                window="secondary",
                used_percent=20.0,
                reset_at=secondary_reset,
                window_minutes=10080,
            )
        )

    async with SessionLocal() as session:
        session.add_all(entries)
        await session.commit()


async def _seed_upstream_usage_with_statuses(*, now) -> None:
    suffix = str(int(now.timestamp() * 1_000_000))
    active_id = f"acc-plus-active-{suffix}"
    paused_id = f"acc-plus-paused-{suffix}"
    deactivated_id = f"acc-plus-deactivated-{suffix}"

    async with SessionLocal() as session:
        session.add_all(
            [
                Account(
                    id=active_id,
                    chatgpt_account_id=f"chatgpt-plus-active-{suffix}",
                    email=f"plus-active-{suffix}@example.com",
                    plan_type="plus",
                    access_token_encrypted=b"a",
                    refresh_token_encrypted=b"b",
                    id_token_encrypted=b"c",
                    last_refresh=now,
                    status=AccountStatus.ACTIVE,
                    reset_at=None,
                ),
                Account(
                    id=paused_id,
                    chatgpt_account_id=f"chatgpt-plus-paused-{suffix}",
                    email=f"plus-paused-{suffix}@example.com",
                    plan_type="plus",
                    access_token_encrypted=b"d",
                    refresh_token_encrypted=b"e",
                    id_token_encrypted=b"f",
                    last_refresh=now,
                    status=AccountStatus.PAUSED,
                    reset_at=None,
                ),
                Account(
                    id=deactivated_id,
                    chatgpt_account_id=f"chatgpt-plus-deactivated-{suffix}",
                    email=f"plus-deactivated-{suffix}@example.com",
                    plan_type="plus",
                    access_token_encrypted=b"g",
                    refresh_token_encrypted=b"h",
                    id_token_encrypted=b"i",
                    last_refresh=now,
                    status=AccountStatus.DEACTIVATED,
                    reset_at=None,
                ),
                UsageHistory(
                    account_id=active_id,
                    recorded_at=now,
                    window="primary",
                    used_percent=20.0,
                    reset_at=int((now + timedelta(hours=4)).timestamp()),
                    window_minutes=300,
                ),
                UsageHistory(
                    account_id=paused_id,
                    recorded_at=now,
                    window="primary",
                    used_percent=100.0,
                    reset_at=int((now + timedelta(hours=4)).timestamp()),
                    window_minutes=300,
                ),
                UsageHistory(
                    account_id=deactivated_id,
                    recorded_at=now,
                    window="primary",
                    used_percent=60.0,
                    reset_at=int((now + timedelta(hours=4)).timestamp()),
                    window_minutes=300,
                ),
                UsageHistory(
                    account_id=active_id,
                    recorded_at=now,
                    window="secondary",
                    used_percent=25.0,
                    reset_at=int((now + timedelta(days=6)).timestamp()),
                    window_minutes=10080,
                ),
                UsageHistory(
                    account_id=paused_id,
                    recorded_at=now,
                    window="secondary",
                    used_percent=100.0,
                    reset_at=int((now + timedelta(days=6)).timestamp()),
                    window_minutes=10080,
                ),
                UsageHistory(
                    account_id=deactivated_id,
                    recorded_at=now,
                    window="secondary",
                    used_percent=75.0,
                    reset_at=int((now + timedelta(days=6)).timestamp()),
                    window_minutes=10080,
                ),
            ]
        )
        await session.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("headers", "expected_message"),
    [
        ({}, "Missing API key in Authorization header"),
        ({"Authorization": "Bearer invalid-key"}, "Invalid API key"),
    ],
)
async def test_v1_usage_requires_valid_bearer_key_when_global_auth_disabled(async_client, headers, expected_message):
    response = await async_client.get("/v1/usage", headers=headers)

    assert response.status_code == 401
    payload = response.json()
    assert payload["error"]["code"] == "invalid_api_key"
    assert payload["error"]["message"] == expected_message


@pytest.mark.asyncio
async def test_v1_usage_returns_zero_usage_for_key_without_logs(async_client):
    _, plain_key = await _create_api_key(name="zero-usage")

    response = await async_client.get("/v1/usage", headers={"Authorization": f"Bearer {plain_key}"})

    assert response.status_code == 200
    assert response.json() == {
        "request_count": 0,
        "total_tokens": 0,
        "cached_input_tokens": 0,
        "total_cost_usd": 0.0,
        "limits": [],
        "upstream_limits": [],
        "member": None,
    }


@pytest.mark.asyncio
async def test_v1_usage_scopes_usage_and_limits_to_authenticated_key(async_client):
    key_a_id, key_a = await _create_api_key(
        name="usage-key-a",
        limits=[LimitRuleInput(limit_type="total_tokens", limit_window="weekly", max_value=1_000)],
    )
    key_b_id, _ = await _create_api_key(
        name="usage-key-b",
        limits=[LimitRuleInput(limit_type="cost_usd", limit_window="monthly", max_value=5_000_000)],
    )

    now = utcnow()
    limit_a_total_reset = now + timedelta(days=6)
    limit_a_cost_reset = now + timedelta(days=20)
    limit_b_reset = now + timedelta(days=20)

    async with SessionLocal() as session:
        repo = ApiKeysRepository(session)
        await repo.replace_limits(
            key_a_id,
            [
                ApiKeyLimit(
                    api_key_id=key_a_id,
                    limit_type=LimitType.TOTAL_TOKENS,
                    limit_window=LimitWindow.WEEKLY,
                    max_value=1_000,
                    current_value=420,
                    model_filter="gpt-5.4",
                    reset_at=limit_a_total_reset,
                ),
                ApiKeyLimit(
                    api_key_id=key_a_id,
                    limit_type=LimitType.COST_USD,
                    limit_window=LimitWindow.MONTHLY,
                    max_value=10_000_000,
                    current_value=2_500_000,
                    model_filter=None,
                    reset_at=limit_a_cost_reset,
                ),
            ],
        )
        await repo.replace_limits(
            key_b_id,
            [
                ApiKeyLimit(
                    api_key_id=key_b_id,
                    limit_type=LimitType.COST_USD,
                    limit_window=LimitWindow.MONTHLY,
                    max_value=5_000_000,
                    current_value=5,
                    model_filter=None,
                    reset_at=limit_b_reset,
                )
            ],
        )

        logs = RequestLogsRepository(session)
        await logs.add_log(
            account_id=None,
            api_key_id=key_a_id,
            request_id="req_v1_usage_a1",
            model="gpt-5.4",
            input_tokens=100,
            output_tokens=25,
            cached_input_tokens=20,
            latency_ms=100,
            status="success",
            error_code=None,
            requested_at=now - timedelta(minutes=2),
        )
        await logs.add_log(
            account_id=None,
            api_key_id=key_a_id,
            request_id="req_v1_usage_a2",
            model="gpt-5.4",
            input_tokens=10,
            output_tokens=5,
            cached_input_tokens=2,
            latency_ms=80,
            status="success",
            error_code=None,
            requested_at=now - timedelta(minutes=1),
        )
        await logs.add_log(
            account_id=None,
            api_key_id=key_b_id,
            request_id="req_v1_usage_b1",
            model="gpt-5.4-mini",
            input_tokens=999,
            output_tokens=111,
            cached_input_tokens=50,
            latency_ms=90,
            status="success",
            error_code=None,
            requested_at=now - timedelta(minutes=3),
        )

    response = await async_client.get("/v1/usage", headers={"Authorization": f"Bearer {key_a}"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["request_count"] == 2
    assert payload["total_tokens"] == 140
    assert payload["cached_input_tokens"] == 22
    assert payload["total_cost_usd"] > 0
    assert payload["limits"] == [
        {
            "limit_type": "total_tokens",
            "limit_window": "weekly",
            "max_value": 1000,
            "current_value": 420,
            "remaining_value": 580,
            "model_filter": "gpt-5.4",
            "reset_at": limit_a_total_reset.isoformat() + "Z",
            "source": "api_key_limit",
        },
        {
            "limit_type": "cost_usd",
            "limit_window": "monthly",
            "max_value": 10000000,
            "current_value": 2500000,
            "remaining_value": 7500000,
            "model_filter": None,
            "reset_at": limit_a_cost_reset.isoformat() + "Z",
            "source": "api_key_limit",
        },
    ]


@pytest.mark.asyncio
async def test_v1_usage_still_works_when_global_api_key_auth_is_disabled(async_client):
    _, plain_key = await _create_api_key(name="self-usage-auth-disabled")

    response = await async_client.get("/v1/usage", headers={"Authorization": f"Bearer {plain_key}"})

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_v1_usage_returns_aggregate_credit_limits_when_upstream_usage_exists(async_client):
    _, plain_key = await _create_api_key(name="fallback-aggregate")
    now = utcnow()
    await _seed_upstream_usage(now=now)

    response = await async_client.get("/v1/usage", headers={"Authorization": f"Bearer {plain_key}"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["limits"] == []
    assert payload["upstream_limits"][0] == {
        "limit_type": "credits",
        "limit_window": "5h",
        "max_value": 450,
        "current_value": 68,
        "remaining_value": 382,
        "model_filter": None,
        "reset_at": payload["upstream_limits"][0]["reset_at"],
        "source": "aggregate",
    }
    assert payload["upstream_limits"][1] == {
        "limit_type": "credits",
        "limit_window": "7d",
        "max_value": 15120,
        "current_value": 3780,
        "remaining_value": 11340,
        "model_filter": None,
        "reset_at": payload["upstream_limits"][1]["reset_at"],
        "source": "aggregate",
    }
    assert payload["upstream_limits"][0]["reset_at"].endswith("Z")
    assert payload["upstream_limits"][1]["reset_at"].endswith("Z")


@pytest.mark.asyncio
async def test_v1_usage_returns_api_key_and_upstream_credit_limits_separately(async_client):
    key_id, plain_key = await _create_api_key(
        name="credit-override",
        limits=[
            LimitRuleInput(limit_type="credits", limit_window="5h", max_value=60),
            LimitRuleInput(limit_type="credits", limit_window="7d", max_value=1000),
        ],
    )
    now = utcnow()
    await _seed_upstream_usage(now=now)

    async with SessionLocal() as session:
        repo = ApiKeysRepository(session)
        await repo.replace_limits(
            key_id,
            [
                ApiKeyLimit(
                    api_key_id=key_id,
                    limit_type=LimitType.CREDITS,
                    limit_window=LimitWindow.FIVE_HOURS,
                    max_value=60,
                    current_value=999,
                    model_filter=None,
                    reset_at=now + timedelta(hours=5),
                ),
                ApiKeyLimit(
                    api_key_id=key_id,
                    limit_type=LimitType.CREDITS,
                    limit_window=LimitWindow.SEVEN_DAYS,
                    max_value=1000,
                    current_value=10,
                    model_filter=None,
                    reset_at=now + timedelta(days=7),
                ),
            ],
        )

    response = await async_client.get("/v1/usage", headers={"Authorization": f"Bearer {plain_key}"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["limits"] == [
        {
            "limit_type": "credits",
            "limit_window": "5h",
            "max_value": 60,
            "current_value": 60,
            "remaining_value": 0,
            "model_filter": None,
            "reset_at": payload["limits"][0]["reset_at"],
            "source": "api_key_limit",
        },
        {
            "limit_type": "credits",
            "limit_window": "7d",
            "max_value": 1000,
            "current_value": 10,
            "remaining_value": 990,
            "model_filter": None,
            "reset_at": payload["limits"][1]["reset_at"],
            "source": "api_key_limit",
        },
    ]
    assert [limit["source"] for limit in payload["upstream_limits"]] == ["aggregate", "aggregate"]
    assert [limit["limit_window"] for limit in payload["upstream_limits"]] == ["5h", "7d"]


@pytest.mark.asyncio
async def test_v1_usage_prefers_raw_limits_when_aggregate_credit_pair_is_partial(async_client):
    key_id, plain_key = await _create_api_key(
        name="fallback-partial-raw",
        limits=[
            LimitRuleInput(limit_type="total_tokens", limit_window="daily", max_value=300),
            LimitRuleInput(limit_type="total_tokens", limit_window="weekly", max_value=1000),
        ],
    )
    now = utcnow()
    await _seed_upstream_usage_partial(now=now, windows=("primary",))

    daily_reset = now + timedelta(hours=2)
    weekly_reset = now + timedelta(days=4)

    async with SessionLocal() as session:
        repo = ApiKeysRepository(session)
        await repo.replace_limits(
            key_id,
            [
                ApiKeyLimit(
                    api_key_id=key_id,
                    limit_type=LimitType.TOTAL_TOKENS,
                    limit_window=LimitWindow.DAILY,
                    max_value=300,
                    current_value=50,
                    model_filter=None,
                    reset_at=daily_reset,
                ),
                ApiKeyLimit(
                    api_key_id=key_id,
                    limit_type=LimitType.TOTAL_TOKENS,
                    limit_window=LimitWindow.WEEKLY,
                    max_value=1000,
                    current_value=200,
                    model_filter=None,
                    reset_at=weekly_reset,
                ),
            ],
        )

    response = await async_client.get("/v1/usage", headers={"Authorization": f"Bearer {plain_key}"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["limits"] == [
        {
            "limit_type": "total_tokens",
            "limit_window": "daily",
            "max_value": 300,
            "current_value": 50,
            "remaining_value": 250,
            "model_filter": None,
            "reset_at": daily_reset.isoformat() + "Z",
            "source": "api_key_limit",
        },
        {
            "limit_type": "total_tokens",
            "limit_window": "weekly",
            "max_value": 1000,
            "current_value": 200,
            "remaining_value": 800,
            "model_filter": None,
            "reset_at": weekly_reset.isoformat() + "Z",
            "source": "api_key_limit",
        },
    ]
    assert [limit["limit_window"] for limit in payload["upstream_limits"]] == ["5h"]


@pytest.mark.asyncio
async def test_v1_usage_falls_back_to_raw_credit_limits_when_aggregate_reset_is_missing(async_client):
    key_id, plain_key = await _create_api_key(
        name="fallback-missing-reset",
        limits=[
            LimitRuleInput(limit_type="credits", limit_window="5h", max_value=60),
            LimitRuleInput(limit_type="credits", limit_window="7d", max_value=1000),
        ],
    )
    now = utcnow()
    await _seed_upstream_usage_partial(now=now, windows=("primary", "secondary"), primary_reset_at=None)

    primary_reset = now + timedelta(hours=5)
    secondary_reset = now + timedelta(days=7)

    async with SessionLocal() as session:
        repo = ApiKeysRepository(session)
        await repo.replace_limits(
            key_id,
            [
                ApiKeyLimit(
                    api_key_id=key_id,
                    limit_type=LimitType.CREDITS,
                    limit_window=LimitWindow.FIVE_HOURS,
                    max_value=60,
                    current_value=12,
                    model_filter=None,
                    reset_at=primary_reset,
                ),
                ApiKeyLimit(
                    api_key_id=key_id,
                    limit_type=LimitType.CREDITS,
                    limit_window=LimitWindow.SEVEN_DAYS,
                    max_value=1000,
                    current_value=250,
                    model_filter=None,
                    reset_at=secondary_reset,
                ),
            ],
        )
        await session.commit()

    response = await async_client.get("/v1/usage", headers={"Authorization": f"Bearer {plain_key}"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["limits"] == [
        {
            "limit_type": "credits",
            "limit_window": "5h",
            "max_value": 60,
            "current_value": 12,
            "remaining_value": 48,
            "model_filter": None,
            "reset_at": primary_reset.isoformat() + "Z",
            "source": "api_key_limit",
        },
        {
            "limit_type": "credits",
            "limit_window": "7d",
            "max_value": 1000,
            "current_value": 250,
            "remaining_value": 750,
            "model_filter": None,
            "reset_at": secondary_reset.isoformat() + "Z",
            "source": "api_key_limit",
        },
    ]
    assert [limit["limit_window"] for limit in payload["upstream_limits"]] == ["7d"]


@pytest.mark.asyncio
async def test_v1_usage_ignores_paused_and_deactivated_accounts_in_aggregate_credit_windows(async_client):
    _, plain_key = await _create_api_key(name="aggregate-active-only")
    now = utcnow()
    await _seed_upstream_usage_with_statuses(now=now)

    response = await async_client.get("/v1/usage", headers={"Authorization": f"Bearer {plain_key}"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["limits"] == []
    assert payload["upstream_limits"] == [
        {
            "limit_type": "credits",
            "limit_window": "5h",
            "max_value": 225,
            "current_value": 45,
            "remaining_value": 180,
            "model_filter": None,
            "reset_at": payload["upstream_limits"][0]["reset_at"],
            "source": "aggregate",
        },
        {
            "limit_type": "credits",
            "limit_window": "7d",
            "max_value": 7560,
            "current_value": 1890,
            "remaining_value": 5670,
            "model_filter": None,
            "reset_at": payload["upstream_limits"][1]["reset_at"],
            "source": "aggregate",
        },
    ]


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
