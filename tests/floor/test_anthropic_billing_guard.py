from __future__ import annotations

from datetime import timedelta, timezone

import pytest
from sqlalchemy import select

import app.modules.proxy.anthropic_service as anthropic_proxy_module
from app.core.anthropic.models import AnthropicMessageRequest
from app.core.utils.time import utcnow
from app.db.models import AdditionalUsageHistory, StickySessionKind
from app.db.session import SessionLocal
from app.db.session_models import StickySession
from tests.floor.anthropic_proxy_support import (  # noqa: I001  # fmt: skip
    _ALLOWED_BODY,
    _BILLED_BODY,
    _OVERAGE_BILLING_HEADERS,
    ANTHROPIC_SSE_BYTES,
    _FakeResponse,
    _FakeResponseContext,
    _insert_account,
    _insert_primary_usage,
    _insert_quota_cooldown,
    _insert_weekly_usage,
    _messages_payload,
    _pin_sticky,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_credit_billing_response_trips_cooldown_and_rotates_next_request(async_client, monkeypatch):

    await _insert_account(
        account_id="anthropic-tripwire",
        provider="anthropic",
        access_token="anthropic-access-tripwire",
        email="tripwire@example.com",
    )
    await _insert_account(
        account_id="anthropic-fallback",
        provider="anthropic",
        access_token="anthropic-access-fallback",
        email="fallback@example.com",
    )
    reset_at = int((utcnow() + timedelta(hours=2)).replace(tzinfo=timezone.utc).timestamp())
    serving_tokens: list[str] = []

    def fake_open_upstream_response(self, session, *, provider_name, headers, json_body):
        del self, session, provider_name, json_body
        token = headers["Authorization"]
        serving_tokens.append(token)
        response_headers = {}
        if token == "Bearer anthropic-access-tripwire":
            response_headers = dict(_OVERAGE_BILLING_HEADERS)
            response_headers["anthropic-ratelimit-unified-reset"] = str(reset_at)
        return _FakeResponseContext(_FakeResponse(200, ANTHROPIC_SSE_BYTES, headers=response_headers))

    monkeypatch.setattr(
        anthropic_proxy_module.AnthropicProxyService,
        "_open_upstream_response",
        fake_open_upstream_response,
    )

    payload = {
        "model": "claude-fable-5",
        "max_tokens": 32,
        "messages": [{"role": "user", "content": "hello"}],
        "thinking": {"type": "adaptive"},
    }
    headers = {
        "anthropic-beta": "oauth-2025-04-20",
        "x-claude-session-id": "tripwire-session",
    }

    request = AnthropicMessageRequest.model_validate(payload)
    sticky_key = anthropic_proxy_module._anthropic_sticky_key(
        request,
        headers,
        quota_key=anthropic_proxy_module._messages_affinity_quota_key(request, provider_name="anthropic"),
    )
    assert sticky_key is not None
    async with SessionLocal() as session:
        session.add(
            StickySession(
                key=sticky_key,
                account_id="anthropic-tripwire",
                kind=StickySessionKind.CODEX_SESSION,
            )
        )
        await session.commit()

    first = await async_client.post("/v1/messages", json=payload, headers=headers)
    assert first.status_code == 200
    assert serving_tokens == [
        "Bearer anthropic-access-tripwire",
        "Bearer anthropic-access-fallback",
    ]
    assert first.content == ANTHROPIC_SSE_BYTES

    async with SessionLocal() as session:
        result = await session.execute(
            select(AdditionalUsageHistory).where(AdditionalUsageHistory.account_id == "anthropic-tripwire")
        )
        cooldown_rows = list(result.scalars())
    assert cooldown_rows, "tripwire must record a quota cooldown"
    latest = max(cooldown_rows, key=lambda row: row.recorded_at)
    assert latest.used_percent == 100.0
    assert latest.reset_at == reset_at
    assert latest.quota_key == anthropic_proxy_module._ANTHROPIC_EXTRA_USAGE_QUOTA_KEY

    second = await async_client.post("/v1/messages", json=payload, headers=headers)
    assert second.status_code == 200
    assert serving_tokens[-1] == "Bearer anthropic-access-fallback"


@pytest.mark.asyncio
async def test_weekly_exhausted_account_is_not_selected_after_primary_recovers(async_client, monkeypatch):

    from app.core.config.settings import get_settings

    monkeypatch.delenv("AGENT_LB_ANTHROPIC_ROUTE_TO_EXTRA_USAGE", raising=False)
    get_settings.cache_clear()
    spent_id = "anthropic-weekly-spent"
    healthy_id = "anthropic-weekly-healthy"
    await _insert_account(
        account_id=spent_id,
        provider="anthropic",
        access_token="anthropic-access-weekly-spent",
        email="weekly-spent@example.com",
    )
    await _insert_account(
        account_id=healthy_id,
        provider="anthropic",
        access_token="anthropic-access-weekly-healthy",
        email="weekly-healthy@example.com",
    )
    weekly_reset = int((utcnow() + timedelta(days=5)).replace(tzinfo=timezone.utc).timestamp())
    primary_reset = int((utcnow() + timedelta(hours=4)).replace(tzinfo=timezone.utc).timestamp())
    await _insert_quota_cooldown(
        account_id=spent_id,
        quota_key=anthropic_proxy_module._ANTHROPIC_EXTRA_USAGE_QUOTA_KEY,
        reset_at=primary_reset,
        recorded_at=utcnow() - timedelta(minutes=10),
    )
    await _insert_primary_usage(account_id=spent_id, used_percent=8.0, reset_at=primary_reset)
    await _insert_weekly_usage(account_id=spent_id, used_percent=100.0, reset_at=weekly_reset)
    await _insert_primary_usage(account_id=healthy_id, used_percent=8.0)
    await _insert_weekly_usage(account_id=healthy_id, used_percent=12.0)
    payload = _messages_payload()
    await _pin_sticky(spent_id, payload)
    upstream_calls: list[str] = []

    def fake_open_upstream_response(self, session, *, provider_name, headers, json_body):
        del self, session, provider_name, json_body
        upstream_calls.append(headers["Authorization"])
        return _FakeResponseContext(_FakeResponse(200, _ALLOWED_BODY))

    monkeypatch.setattr(
        anthropic_proxy_module.AnthropicProxyService,
        "_open_upstream_response",
        fake_open_upstream_response,
    )

    response = await async_client.post(
        "/v1/messages",
        json=payload,
        headers={"anthropic-beta": "oauth-2025-04-20", "x-claude-session-id": f"session-{spent_id}"},
    )

    assert response.status_code == 200
    assert response.content == _ALLOWED_BODY
    assert upstream_calls == ["Bearer anthropic-access-weekly-healthy"]


@pytest.mark.asyncio
async def test_weekly_exhausted_only_account_returns_usage_limit_with_weekly_reset(async_client, monkeypatch):
    from app.core.config.settings import get_settings

    monkeypatch.delenv("AGENT_LB_ANTHROPIC_ROUTE_TO_EXTRA_USAGE", raising=False)
    get_settings.cache_clear()
    await _insert_account(
        account_id="anthropic-weekly-only",
        provider="anthropic",
        access_token="anthropic-access-weekly-only",
        email="weekly-only@example.com",
    )
    weekly_reset = int((utcnow() + timedelta(days=4)).replace(tzinfo=timezone.utc).timestamp())
    primary_reset = int((utcnow() + timedelta(hours=3)).replace(tzinfo=timezone.utc).timestamp())
    await _insert_quota_cooldown(
        account_id="anthropic-weekly-only",
        quota_key=anthropic_proxy_module._ANTHROPIC_EXTRA_USAGE_QUOTA_KEY,
        reset_at=primary_reset,
        recorded_at=utcnow() - timedelta(minutes=10),
    )
    await _insert_primary_usage(account_id="anthropic-weekly-only", used_percent=8.0, reset_at=primary_reset)
    await _insert_weekly_usage(account_id="anthropic-weekly-only", used_percent=100.0, reset_at=weekly_reset)
    upstream_calls: list[str] = []

    def fake_open_upstream_response(self, session, *, provider_name, headers, json_body):
        del self, session, provider_name, json_body
        upstream_calls.append(headers["Authorization"])
        return _FakeResponseContext(_FakeResponse(200, _BILLED_BODY))

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
    assert response.headers["anthropic-ratelimit-unified-reset"] == str(weekly_reset)
    assert response.json()["error"]["type"] == "rate_limit_error"
    assert upstream_calls == []
    assert _BILLED_BODY not in response.content
