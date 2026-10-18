from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

import app.modules.proxy.anthropic_service as anthropic_proxy_module
from app.core.anthropic.models import AnthropicMessageRequest
from app.core.utils.time import utcnow
from app.db.models import AdditionalUsageHistory, StickySession, StickySessionKind
from app.db.session import SessionLocal
from tests.integration.anthropic_proxy_support import (
    ANTHROPIC_JSON_BYTES,
    _FakeResponse,
    _FakeResponseContext,
    _insert_account,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_anthropic_fast_429_records_fast_cooldown_and_allows_standard_fallback(
    async_client,
    monkeypatch,
):
    await _insert_account(
        account_id="anthropic-fast-a",
        provider="anthropic",
        access_token="anthropic-fast-access-a",
        email="claude-a@example.com",
    )
    await _insert_account(
        account_id="anthropic-fast-b",
        provider="anthropic",
        access_token="anthropic-fast-access-b",
        email="claude-b@example.com",
    )

    session_id = "claude-fast-fallback"
    payload = {
        "model": "claude-fable-5",
        "max_tokens": 32,
        "messages": [{"role": "user", "content": "fast then fallback"}],
        "thinking": {"type": "adaptive"},
    }
    request = AnthropicMessageRequest.model_validate(payload)
    affinity_quota_key = anthropic_proxy_module._messages_affinity_quota_key(request, provider_name="anthropic")
    sticky_key = anthropic_proxy_module._messages_sticky_key(
        request,
        {"x-claude-session-id": session_id},
        provider_name="anthropic",
        quota_key=affinity_quota_key,
    )
    async with SessionLocal() as session:
        session.add(
            StickySession(
                key=sticky_key,
                account_id="anthropic-fast-a",
                kind=StickySessionKind.CODEX_SESSION,
            )
        )
        await session.commit()

    seen: list[tuple[str, str | None]] = []
    cooldown_reset_at = int((utcnow() + timedelta(minutes=10)).replace(tzinfo=timezone.utc).timestamp())
    cooldown_reset_header = datetime.fromtimestamp(cooldown_reset_at, tz=timezone.utc).isoformat()

    def fake_open_upstream_response(self, session, *, provider_name, headers, json_body):
        del self, session
        assert provider_name == "anthropic"
        authorization = headers["Authorization"]
        speed = json_body.get("speed")
        seen.append((authorization, speed if isinstance(speed, str) else None))
        if authorization == "Bearer anthropic-fast-access-a" and speed == "fast":
            return _FakeResponseContext(
                _FakeResponse(
                    429,
                    b'{"error":{"message":"fast pool exhausted"}}',
                    headers={
                        "anthropic-fast-input-tokens-reset": cooldown_reset_header,
                    },
                )
            )
        return _FakeResponseContext(_FakeResponse(200, ANTHROPIC_JSON_BYTES))

    monkeypatch.setattr(
        anthropic_proxy_module.AnthropicProxyService,
        "_open_upstream_response",
        fake_open_upstream_response,
    )

    fast_response = await async_client.post(
        "/v1/messages",
        json={**payload, "speed": "fast"},
        headers={
            "anthropic-beta": "oauth-2025-04-20,fast-mode-2026-02-01",
            "x-claude-session-id": session_id,
        },
    )
    fallback_response = await async_client.post(
        "/v1/messages",
        json=payload,
        headers={
            "anthropic-beta": "oauth-2025-04-20,fast-mode-2026-02-01",
            "x-claude-session-id": session_id,
        },
    )

    assert fast_response.status_code == 200
    assert fallback_response.status_code == 200
    assert seen == [
        ("Bearer anthropic-fast-access-a", "fast"),
        ("Bearer anthropic-fast-access-b", "fast"),
        ("Bearer anthropic-fast-access-b", None),
    ]

    async with SessionLocal() as session:
        fast_cooldowns = (
            (
                await session.execute(
                    select(AdditionalUsageHistory).where(
                        AdditionalUsageHistory.quota_key == "anthropic_fast",
                        AdditionalUsageHistory.window == "primary",
                    )
                )
            )
            .scalars()
            .all()
        )
        model_cooldowns = (
            (
                await session.execute(
                    select(AdditionalUsageHistory).where(
                        AdditionalUsageHistory.quota_key == "anthropic_top_thinking",
                        AdditionalUsageHistory.window == "primary",
                    )
                )
            )
            .scalars()
            .all()
        )
        sticky = (
            await session.execute(
                select(StickySession).where(
                    StickySession.key == sticky_key,
                    StickySession.kind == StickySessionKind.CODEX_SESSION,
                )
            )
        ).scalar_one()

    by_account = {entry.account_id: entry for entry in fast_cooldowns}
    assert by_account["anthropic-fast-a"].used_percent == 100.0
    assert by_account["anthropic-fast-a"].reset_at == cooldown_reset_at
    assert by_account["anthropic-fast-b"].used_percent == 0.0
    assert all(entry.used_percent == 0.0 for entry in model_cooldowns)
    assert sticky.account_id == "anthropic-fast-b"

    accounts_response = await async_client.get("/api/accounts")
    assert accounts_response.status_code == 200
    anthropic_a = next(
        account for account in accounts_response.json()["accounts"] if account["accountId"] == "anthropic-fast-a"
    )
    fast_quota = next(quota for quota in anthropic_a["additionalQuotas"] if quota["quotaKey"] == "anthropic_fast")
    assert fast_quota["displayLabel"] == "Claude fast mode"
    assert fast_quota["primaryWindow"]["usedPercent"] == 100.0
