from __future__ import annotations

from datetime import timedelta, timezone

import pytest
from sqlalchemy import select

import app.modules.proxy.anthropic_service as anthropic_proxy_module
from app.core.utils.time import utcnow
from app.db.models import AdditionalUsageHistory, RequestLog
from app.db.session import SessionLocal
from app.dependencies import _proxy_repo_context
from tests.floor.anthropic_proxy_support import _ALLOWED_BODY, _BILLED_BODY, _BodyTrackingResponse, _FakeResponse, _FakeResponseContext, _insert_account, _insert_primary_usage, _messages_payload, _pin_sticky  # noqa: I001  # fmt: skip

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_overage_200_fails_over_without_forwarding_billed_body(async_client, monkeypatch):
    from app.core.config.settings import get_settings

    monkeypatch.delenv("AGENT_LB_ANTHROPIC_ROUTE_TO_EXTRA_USAGE", raising=False)
    get_settings.cache_clear()
    await _insert_account(
        account_id="anthropic-overage-a",
        provider="anthropic",
        access_token="anthropic-access-overage-a",
        email="overage-a@example.com",
    )
    await _insert_account(
        account_id="anthropic-overage-b",
        provider="anthropic",
        access_token="anthropic-access-overage-b",
        email="overage-b@example.com",
    )
    reset_at = int((utcnow() + timedelta(hours=2)).replace(tzinfo=timezone.utc).timestamp())
    payload = _messages_payload()
    await _pin_sticky("anthropic-overage-a", payload)
    billed = _BodyTrackingResponse(
        200,
        _BILLED_BODY,
        headers={
            "anthropic-ratelimit-unified-overage-in-use": "true",
            "anthropic-ratelimit-unified-reset": str(reset_at),
        },
    )

    def fake_open_upstream_response(self, session, *, provider_name, headers, json_body):
        del self, session, provider_name, json_body
        if headers["Authorization"] == "Bearer anthropic-access-overage-a":
            return _FakeResponseContext(billed)
        return _FakeResponseContext(_FakeResponse(200, _ALLOWED_BODY))

    monkeypatch.setattr(
        anthropic_proxy_module.AnthropicProxyService,
        "_open_upstream_response",
        fake_open_upstream_response,
    )

    response = await async_client.post(
        "/v1/messages",
        json=payload,
        headers={"anthropic-beta": "oauth-2025-04-20", "x-claude-session-id": "session-anthropic-overage-a"},
    )

    assert response.status_code == 200
    assert response.content == _ALLOWED_BODY
    assert billed.body_consumed is False
    async with SessionLocal() as session:
        logs = list(
            (await session.execute(select(RequestLog).where(RequestLog.account_id == "anthropic-overage-a"))).scalars()
        )
        cooldown = (
            await session.execute(
                select(AdditionalUsageHistory).where(AdditionalUsageHistory.account_id == "anthropic-overage-a")
            )
        ).scalars()
        cooldown_rows = list(cooldown)
    refused = [row for row in logs if row.status == "error" and row.error_code == "extra_usage_refused"]
    assert len(logs) == 1
    assert len(refused) == 1
    assert any(
        row.quota_key == anthropic_proxy_module._ANTHROPIC_EXTRA_USAGE_QUOTA_KEY and row.reset_at == reset_at
        for row in cooldown_rows
    )


@pytest.mark.asyncio
async def test_overage_200_only_account_returns_usage_limit_not_body(async_client, monkeypatch):
    from app.core.config.settings import get_settings

    monkeypatch.delenv("AGENT_LB_ANTHROPIC_ROUTE_TO_EXTRA_USAGE", raising=False)
    get_settings.cache_clear()
    await _insert_account(
        account_id="anthropic-overage-only",
        provider="anthropic",
        access_token="anthropic-access-overage-only",
        email="overage-only@example.com",
    )
    reset_at = int((utcnow() + timedelta(hours=2)).replace(tzinfo=timezone.utc).timestamp())
    billed = _BodyTrackingResponse(
        200,
        _BILLED_BODY,
        headers={
            "anthropic-ratelimit-unified-overage-in-use": "true",
            "anthropic-ratelimit-unified-reset": str(reset_at),
        },
    )

    def fake_open_upstream_response(self, session, *, provider_name, headers, json_body):
        del self, session, provider_name, json_body
        return _FakeResponseContext(billed)

    monkeypatch.setattr(
        anthropic_proxy_module.AnthropicProxyService,
        "_open_upstream_response",
        fake_open_upstream_response,
    )

    response = await async_client.post(
        "/v1/messages",
        json=_messages_payload(),
        headers={"anthropic-beta": "oauth-2025-04-20"},
    )

    assert response.status_code == 429
    assert response.headers["anthropic-ratelimit-unified-reset"] == str(reset_at)
    assert response.json()["error"]["type"] == "rate_limit_error"
    assert billed.body_consumed is False
    assert _BILLED_BODY not in response.content


@pytest.mark.asyncio
async def test_overage_200_is_streamed_when_routing_on_and_account_is_not_paid_fallback(async_client, monkeypatch):

    from app.core.config.settings import get_settings

    monkeypatch.setenv("AGENT_LB_ANTHROPIC_ROUTE_TO_EXTRA_USAGE", "true")
    get_settings.cache_clear()
    account_id = "anthropic-overage-routed"
    await _insert_account(
        account_id=account_id,
        provider="anthropic",
        access_token="anthropic-access-overage-routed",
        email="overage-routed@example.com",
    )
    reset_at = int((utcnow() + timedelta(hours=2)).replace(tzinfo=timezone.utc).timestamp())
    await _insert_primary_usage(account_id=account_id, used_percent=8.0, reset_at=reset_at)
    eligibility = await anthropic_proxy_module.AnthropicProxyService(_proxy_repo_context)._provider_quota_eligibility(
        "anthropic", "anthropic_top", model="claude-sonnet-4-20250514"
    )
    assert account_id in eligibility.account_ids
    assert account_id not in eligibility.paid_fallback_account_ids
    billed = _BodyTrackingResponse(
        200,
        _BILLED_BODY,
        headers={
            "anthropic-ratelimit-unified-overage-in-use": "true",
            "anthropic-ratelimit-unified-reset": str(reset_at),
        },
    )

    def fake_open_upstream_response(self, session, *, provider_name, headers, json_body):
        del self, session, provider_name, json_body
        return _FakeResponseContext(billed)

    monkeypatch.setattr(
        anthropic_proxy_module.AnthropicProxyService,
        "_open_upstream_response",
        fake_open_upstream_response,
    )

    response = await async_client.post(
        "/v1/messages",
        json=_messages_payload(),
        headers={"anthropic-beta": "oauth-2025-04-20"},
    )

    assert response.status_code == 200
    assert response.content == _BILLED_BODY
    assert billed.body_consumed is True
    async with SessionLocal() as session:
        cooldown_rows = list(
            (
                await session.execute(
                    select(AdditionalUsageHistory).where(AdditionalUsageHistory.account_id == account_id)
                )
            ).scalars()
        )
    assert any(
        row.quota_key == anthropic_proxy_module._ANTHROPIC_EXTRA_USAGE_QUOTA_KEY and row.reset_at == reset_at
        for row in cooldown_rows
    )
    get_settings.cache_clear()
