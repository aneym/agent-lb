from __future__ import annotations

from datetime import timedelta, timezone

import pytest
from sqlalchemy import select

import app.modules.proxy.anthropic_service as anthropic_proxy_module
from app.core.utils.time import utcnow
from app.db.models import Account, StickySession, StickySessionKind
from app.db.session import SessionLocal
from tests.integration.anthropic_proxy_support import (
    _insert_account,
    _insert_primary_usage,
    _insert_quota_cooldown,
    _insert_weekly_usage,
)
from tests.integration.anthropic_proxy_support import (
    fable_burn_first_enabled as fable_burn_first_enabled,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_fable_requests_route_to_accounts_under_weekly_threshold(async_client):
    await _insert_account(
        account_id="anthropic-weekly-hot",
        provider="anthropic",
        access_token="anthropic-access-hot",
        email="hot@example.com",
    )
    await _insert_account(
        account_id="anthropic-weekly-fresh",
        provider="anthropic",
        access_token="anthropic-access-fresh",
        email="fresh@example.com",
    )
    await _insert_weekly_usage(account_id="anthropic-weekly-hot", used_percent=60.0)
    await _insert_weekly_usage(account_id="anthropic-weekly-fresh", used_percent=10.0)

    response = await async_client.post(
        "/api/anthropic/session-route",
        json={
            "sessionId": "session-fable-threshold",
            "model": "claude-fable-5",
            "quotaKey": "anthropic_top_thinking",
        },
    )

    assert response.status_code == 200
    assert response.json()["accountId"] == "anthropic-weekly-fresh"


@pytest.mark.asyncio
async def test_fable_requests_fall_back_when_all_accounts_over_threshold(async_client):
    for index, used in enumerate((70.0, 90.0)):
        await _insert_account(
            account_id=f"anthropic-weekly-over-{index}",
            provider="anthropic",
            access_token=f"anthropic-access-over-{index}",
            email=f"over-{index}@example.com",
        )
        await _insert_weekly_usage(account_id=f"anthropic-weekly-over-{index}", used_percent=used)

    response = await async_client.post(
        "/api/anthropic/session-route",
        json={
            "sessionId": "session-fable-fallback",
            "model": "claude-fable-5",
            "quotaKey": "anthropic_top_thinking",
        },
    )

    assert response.status_code == 200
    assert response.json()["accountId"].startswith("anthropic-weekly-over-")


@pytest.mark.asyncio
async def test_non_fable_requests_prefer_accounts_over_weekly_threshold(async_client, fable_burn_first_enabled):
    await _insert_account(
        account_id="anthropic-burn-hot",
        provider="anthropic",
        access_token="anthropic-access-burn-hot",
        email="burn-hot@example.com",
    )
    await _insert_account(
        account_id="anthropic-burn-fresh",
        provider="anthropic",
        access_token="anthropic-access-burn-fresh",
        email="burn-fresh@example.com",
    )
    await _insert_weekly_usage(account_id="anthropic-burn-hot", used_percent=60.0)
    await _insert_weekly_usage(account_id="anthropic-burn-fresh", used_percent=10.0)

    response = await async_client.post(
        "/api/anthropic/session-route",
        json={
            "sessionId": "session-haiku-burn",
            "model": "claude-haiku-4-5",
            "quotaKey": "anthropic_standard",
        },
    )

    assert response.status_code == 200
    assert response.json()["accountId"] == "anthropic-burn-hot"


@pytest.mark.asyncio
async def test_non_fable_burn_preference_respects_preserve_policy(async_client):
    await _insert_account(
        account_id="anthropic-preserve-hot",
        provider="anthropic",
        access_token="anthropic-access-preserve-hot",
        email="preserve-hot@example.com",
    )
    await _insert_account(
        account_id="anthropic-preserve-fresh",
        provider="anthropic",
        access_token="anthropic-access-preserve-fresh",
        email="preserve-fresh@example.com",
    )
    async with SessionLocal() as session:
        account = await session.get(Account, "anthropic-preserve-hot")
        account.routing_policy = "preserve"
        await session.commit()
    await _insert_weekly_usage(account_id="anthropic-preserve-hot", used_percent=60.0)
    await _insert_weekly_usage(account_id="anthropic-preserve-fresh", used_percent=10.0)

    response = await async_client.post(
        "/api/anthropic/session-route",
        json={
            "sessionId": "session-haiku-preserve",
            "model": "claude-haiku-4-5",
            "quotaKey": "anthropic_standard",
        },
    )

    assert response.status_code == 200
    assert response.json()["accountId"] == "anthropic-preserve-fresh"


@pytest.mark.asyncio
async def test_mixed_model_session_holds_separate_pins(async_client, fable_burn_first_enabled):

    await _insert_account(
        account_id="anthropic-mixed-hot",
        provider="anthropic",
        access_token="anthropic-access-mixed-hot",
        email="mixed-hot@example.com",
    )
    await _insert_account(
        account_id="anthropic-mixed-fresh",
        provider="anthropic",
        access_token="anthropic-access-mixed-fresh",
        email="mixed-fresh@example.com",
    )
    await _insert_weekly_usage(account_id="anthropic-mixed-hot", used_percent=60.0)
    await _insert_weekly_usage(account_id="anthropic-mixed-fresh", used_percent=10.0)

    session_id = "session-mixed-model-alternation"

    async def _route(model: str) -> str:
        response = await async_client.post(
            "/api/anthropic/session-route",
            json={
                "sessionId": session_id,
                "model": model,
                "quotaKey": "anthropic_top_thinking",
            },
        )
        assert response.status_code == 200
        return response.json()["accountId"]

    assert await _route("claude-fable-5") == "anthropic-mixed-fresh"
    assert await _route("claude-sonnet-5") == "anthropic-mixed-hot"
    assert await _route("claude-fable-5") == "anthropic-mixed-fresh"
    assert await _route("claude-sonnet-5") == "anthropic-mixed-hot"

    session_hash = anthropic_proxy_module._hash_for_key(session_id)
    fable_sticky_key = f"claude:anthropic_top_thinking_fable:session:{session_hash}"
    non_fable_sticky_key = f"claude:anthropic_top_thinking:session:{session_hash}"
    assert fable_sticky_key != non_fable_sticky_key

    async with SessionLocal() as session:
        fable_sticky = (
            await session.execute(
                select(StickySession).where(
                    StickySession.key == fable_sticky_key,
                    StickySession.kind == StickySessionKind.CODEX_SESSION,
                )
            )
        ).scalar_one()
        non_fable_sticky = (
            await session.execute(
                select(StickySession).where(
                    StickySession.key == non_fable_sticky_key,
                    StickySession.kind == StickySessionKind.CODEX_SESSION,
                )
            )
        ).scalar_one()

    assert fable_sticky.account_id == "anthropic-mixed-fresh"
    assert non_fable_sticky.account_id == "anthropic-mixed-hot"


@pytest.mark.asyncio
async def test_non_fable_sticky_session_drains_to_over_threshold_account(
    async_client, monkeypatch, fable_burn_first_enabled
):
    from app.core.config.settings import get_settings

    monkeypatch.setenv("AGENT_LB_ANTHROPIC_STICKY_HOLD_UNTIL_EXHAUSTED", "false")
    get_settings.cache_clear()

    await _insert_account(
        account_id="anthropic-drain-hot",
        provider="anthropic",
        access_token="anthropic-access-drain-hot",
        email="drain-hot@example.com",
    )
    await _insert_account(
        account_id="anthropic-drain-fresh",
        provider="anthropic",
        access_token="anthropic-access-drain-fresh",
        email="drain-fresh@example.com",
    )
    await _insert_weekly_usage(account_id="anthropic-drain-hot", used_percent=60.0)
    await _insert_weekly_usage(account_id="anthropic-drain-fresh", used_percent=10.0)

    session_id = "session-haiku-sticky-drain"
    sticky_key = "claude:anthropic_standard:session:" + anthropic_proxy_module._hash_for_key(session_id)
    async with SessionLocal() as session:
        session.add(
            StickySession(
                key=sticky_key,
                account_id="anthropic-drain-fresh",
                kind=StickySessionKind.CODEX_SESSION,
            )
        )
        await session.commit()

    response = await async_client.post(
        "/api/anthropic/session-route",
        json={
            "sessionId": session_id,
            "model": "claude-haiku-4-5",
            "quotaKey": "anthropic_standard",
        },
    )

    assert response.status_code == 200
    assert response.json()["accountId"] == "anthropic-drain-hot"

    async with SessionLocal() as session:
        sticky = (
            await session.execute(
                select(StickySession).where(
                    StickySession.key == sticky_key,
                    StickySession.kind == StickySessionKind.CODEX_SESSION,
                )
            )
        ).scalar_one()
    assert sticky.account_id == "anthropic-drain-hot"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("live_429", "expected_account"),
    [
        (False, "anthropic-hold-pinned"),
        (True, "anthropic-hold-idle"),
    ],
)
async def test_sticky_session_holds_account_until_exhausted(async_client, live_429, expected_account):
    await _insert_account(
        account_id="anthropic-hold-pinned",
        provider="anthropic",
        access_token="anthropic-access-hold-pinned",
        email="hold-pinned@example.com",
    )
    await _insert_account(
        account_id="anthropic-hold-idle",
        provider="anthropic",
        access_token="anthropic-access-hold-idle",
        email="hold-idle@example.com",
    )
    await _insert_account(
        account_id="anthropic-hold-busy",
        provider="anthropic",
        access_token="anthropic-access-hold-busy",
        email="hold-busy@example.com",
    )
    await _insert_primary_usage(account_id="anthropic-hold-pinned", used_percent=97.0)
    if live_429:
        await _insert_quota_cooldown(
            account_id="anthropic-hold-pinned",
            quota_key="anthropic_standard",
            reset_at=int((utcnow() + timedelta(minutes=30)).replace(tzinfo=timezone.utc).timestamp()),
        )
    await _insert_weekly_usage(account_id="anthropic-hold-pinned", used_percent=10.0)
    await _insert_primary_usage(account_id="anthropic-hold-idle", used_percent=5.0)

    await _insert_primary_usage(account_id="anthropic-hold-busy", used_percent=60.0)
    await _insert_weekly_usage(account_id="anthropic-hold-busy", used_percent=60.0)

    session_id = f"session-hold-{live_429}"
    sticky_key = "claude:anthropic_standard:session:" + anthropic_proxy_module._hash_for_key(session_id)
    async with SessionLocal() as session:
        session.add(
            StickySession(key=sticky_key, account_id="anthropic-hold-pinned", kind=StickySessionKind.CODEX_SESSION)
        )
        await session.commit()

    response = await async_client.post(
        "/api/anthropic/session-route",
        json={"sessionId": session_id, "model": "claude-haiku-4-5", "quotaKey": "anthropic_standard"},
    )

    assert response.status_code == 200
    assert response.json()["accountId"] == expected_account
    async with SessionLocal() as session:
        sticky = (
            await session.execute(
                select(StickySession).where(
                    StickySession.key == sticky_key,
                    StickySession.kind == StickySessionKind.CODEX_SESSION,
                )
            )
        ).scalar_one()
    assert sticky.account_id == expected_account


@pytest.mark.asyncio
async def test_non_fable_sticky_drain_disabled_keeps_pin(async_client, monkeypatch):
    from app.core.config.settings import get_settings

    monkeypatch.setenv("AGENT_LB_ANTHROPIC_FABLE_STICKY_DRAIN_ENABLED", "false")
    get_settings.cache_clear()

    await _insert_account(
        account_id="anthropic-drain-disabled-hot",
        provider="anthropic",
        access_token="anthropic-access-drain-disabled-hot",
        email="drain-disabled-hot@example.com",
    )
    await _insert_account(
        account_id="anthropic-drain-disabled-fresh",
        provider="anthropic",
        access_token="anthropic-access-drain-disabled-fresh",
        email="drain-disabled-fresh@example.com",
    )
    await _insert_weekly_usage(account_id="anthropic-drain-disabled-hot", used_percent=60.0)
    await _insert_weekly_usage(account_id="anthropic-drain-disabled-fresh", used_percent=10.0)

    session_id = "session-haiku-sticky-drain-disabled"
    sticky_key = "claude:anthropic_standard:session:" + anthropic_proxy_module._hash_for_key(session_id)
    async with SessionLocal() as session:
        session.add(
            StickySession(
                key=sticky_key,
                account_id="anthropic-drain-disabled-fresh",
                kind=StickySessionKind.CODEX_SESSION,
            )
        )
        await session.commit()

    response = await async_client.post(
        "/api/anthropic/session-route",
        json={
            "sessionId": session_id,
            "model": "claude-haiku-4-5",
            "quotaKey": "anthropic_standard",
        },
    )

    assert response.status_code == 200
    assert response.json()["accountId"] == "anthropic-drain-disabled-fresh"

    async with SessionLocal() as session:
        sticky = (
            await session.execute(
                select(StickySession).where(
                    StickySession.key == sticky_key,
                    StickySession.kind == StickySessionKind.CODEX_SESSION,
                )
            )
        ).scalar_one()
    assert sticky.account_id == "anthropic-drain-disabled-fresh"


@pytest.mark.asyncio
async def test_non_fable_sticky_session_already_on_drain_account_keeps_pin(async_client):
    await _insert_account(
        account_id="anthropic-drain-already-hot",
        provider="anthropic",
        access_token="anthropic-access-drain-already-hot",
        email="drain-already-hot@example.com",
    )
    await _insert_account(
        account_id="anthropic-drain-already-fresh",
        provider="anthropic",
        access_token="anthropic-access-drain-already-fresh",
        email="drain-already-fresh@example.com",
    )
    await _insert_weekly_usage(account_id="anthropic-drain-already-hot", used_percent=60.0)
    await _insert_weekly_usage(account_id="anthropic-drain-already-fresh", used_percent=10.0)

    session_id = "session-haiku-sticky-drain-already"
    sticky_key = "claude:anthropic_standard:session:" + anthropic_proxy_module._hash_for_key(session_id)
    async with SessionLocal() as session:
        session.add(
            StickySession(
                key=sticky_key,
                account_id="anthropic-drain-already-hot",
                kind=StickySessionKind.CODEX_SESSION,
            )
        )
        await session.commit()

    response = await async_client.post(
        "/api/anthropic/session-route",
        json={
            "sessionId": session_id,
            "model": "claude-haiku-4-5",
            "quotaKey": "anthropic_standard",
        },
    )

    assert response.status_code == 200
    assert response.json()["accountId"] == "anthropic-drain-already-hot"

    async with SessionLocal() as session:
        sticky = (
            await session.execute(
                select(StickySession).where(
                    StickySession.key == sticky_key,
                    StickySession.kind == StickySessionKind.CODEX_SESSION,
                )
            )
        ).scalar_one()
    assert sticky.account_id == "anthropic-drain-already-hot"
