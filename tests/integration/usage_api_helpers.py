from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from app.core.utils.time import utcnow
from app.db.models import (
    Account,
    AccountStatus,
    ApiKey,
    RequestLog,
    UsageHistory,
)
from app.db.session import SessionLocal
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.api_keys.service import ApiKeyCreateData, ApiKeysService, LimitRuleInput
from app.modules.team.service import reset_team_usage_cache


async def _create_member_key(async_client, **member_overrides) -> tuple[dict, str]:

    payload = {"name": f"member-{utcnow().timestamp()}"}
    payload.update(member_overrides)
    created = await async_client.post("/api/team/members", json=payload)
    assert created.status_code == 200, created.text
    member = created.json()
    account_id = f"account-{member['id']}"
    async with SessionLocal() as session:
        session.add(
            Account(
                id=account_id,
                email=f"{account_id}@example.com",
                plan_type="plus",
                access_token_encrypted=b"a",
                refresh_token_encrypted=b"b",
                last_refresh=utcnow(),
                status=AccountStatus.ACTIVE,
            )
        )
        await session.commit()

    issued = await async_client.post(
        f"/api/team/members/{member['id']}/keys", json={"assignedAccountIds": [account_id]}
    )
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
