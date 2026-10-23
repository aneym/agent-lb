from __future__ import annotations

import pytest

from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, RequestLog
from app.db.session import SessionLocal
from app.modules.request_logs.repository import RequestLogsRepository
from app.modules.usage.repository import UsageRepository
from app.modules.usage.service import _LOG_METRICS_CACHE

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_usage_summary_api_scopes_quota_and_costs_by_provider_and_subscription(async_client):
    _LOG_METRICS_CACHE.clear()
    async with SessionLocal() as session:
        usage = UsageRepository(session)
        for account_id, provider, plan, subscription, utilization, cost in (
            ("summary-claude", "anthropic", "claude", "active", 20, 2),
            ("summary-codex", "openai", "plus", "active", 80, 8),
            ("summary-canceled", "anthropic", "claude", "canceled", 100, 100),
        ):
            session.add(
                Account(
                    id=account_id,
                    provider=provider,
                    email=f"{account_id}@example.invalid",
                    plan_type=plan,
                    status=AccountStatus.ACTIVE,
                    subscription_status=subscription,
                    access_token_encrypted=b"access",
                    refresh_token_encrypted=b"refresh",
                    id_token_encrypted=b"id",
                    last_refresh=utcnow(),
                )
            )
            await session.commit()
            for window, minutes in (("primary", 300), ("secondary", 10080)):
                await usage.add_entry(account_id, utilization, provider=provider, window=window, window_minutes=minutes)
            session.add(
                RequestLog(
                    account_id=account_id,
                    provider=provider,
                    request_id=account_id,
                    model="claude-sonnet-4-5" if provider == "anthropic" else "gpt-5.1",
                    status="success",
                    input_tokens=1000,
                    output_tokens=200,
                    cost_usd=cost,
                )
            )
            await session.commit()

    claude = await async_client.get("/api/usage/summary?provider=Anthropic")
    assert claude.status_code == 200, claude.text
    payload = claude.json()
    assert payload["provider"] == "Anthropic"
    assert payload["secondaryWindow"]["remainingPercent"] == 80
    assert payload["cost"]["totalUsd7d"] == 2
    assert payload["metrics"]["requests7d"] == 1

    codex = await async_client.get("/api/usage/summary?provider=openai")
    assert codex.status_code == 200, codex.text
    assert codex.json()["secondaryWindow"]["remainingPercent"] == 20
    assert codex.json()["cost"]["totalUsd7d"] == 8

    total = await async_client.get("/api/usage/summary")
    assert total.status_code == 200, total.text
    assert total.json()["cost"]["totalUsd7d"] == 10
    assert total.json()["metrics"]["requests7d"] == 2


async def test_scoped_usage_counts_cancelled_separately_from_failures(db_setup):
    del db_setup
    now = utcnow()
    async with SessionLocal() as session:
        session.add(
            Account(
                id="scoped-status",
                provider="anthropic",
                email="status@example.invalid",
                plan_type="claude",
                status=AccountStatus.ACTIVE,
                access_token_encrypted=b"access",
                refresh_token_encrypted=b"refresh",
                id_token_encrypted=b"id",
                last_refresh=now,
            )
        )
        await session.commit()
        for status, error_code in (
            ("success", None),
            ("cancelled", "client_disconnected"),
            ("error", "upstream_failed"),
        ):
            session.add(
                RequestLog(
                    account_id="scoped-status",
                    provider="anthropic",
                    request_id=f"status-{status}",
                    model="claude-sonnet-4-5",
                    status=status,
                    error_code=error_code,
                    input_tokens=100,
                    output_tokens=20,
                    cost_usd=1,
                    requested_at=now,
                )
            )
        await session.commit()
        metrics, cost = await RequestLogsRepository(session).aggregate_usage_window(
            now,
            account_ids={"scoped-status"},
            include_unattributed=False,
        )
        assert metrics.requests_7d == 3
        assert metrics.cancelled_7d == 1
        assert metrics.error_rate_7d == pytest.approx(1 / 3)
        assert metrics.top_error == "upstream_failed"
        assert metrics.tokens_secondary_window == 360
        assert cost.total_usd_7d == 3
