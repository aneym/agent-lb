from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

import app.modules.proxy.anthropic_service as anthropic_proxy_module
from app.core.anthropic.models import AnthropicMessageRequest
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, AdditionalUsageHistory, RequestLog, StickySession, StickySessionKind
from app.db.session import SessionLocal
from tests.integration.anthropic_proxy_support import (
    ANTHROPIC_JSON_BYTES,
    _FakeResponse,
    _FakeResponseContext,
    _insert_account,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_anthropic_messages_fails_over_when_refresh_invalid_grant_before_upstream(
    async_client,
    monkeypatch,
):
    await _insert_account(
        account_id="anthropic-stale",
        provider="anthropic",
        access_token="anthropic-access-stale",
        email="stale-claude@example.com",
    )
    await _insert_account(
        account_id="anthropic-healthy",
        provider="anthropic",
        access_token="anthropic-access-healthy",
        email="healthy-claude@example.com",
    )

    payload = {
        "model": "claude-fable-5",
        "max_tokens": 32,
        "messages": [{"role": "user", "content": "fail over stale auth"}],
        "thinking": {"type": "adaptive"},
    }
    request = AnthropicMessageRequest.model_validate(payload)
    sticky_key = anthropic_proxy_module._anthropic_sticky_key(
        request,
        {"x-claude-session-id": "claude-refresh-failover"},
        quota_key=anthropic_proxy_module._messages_affinity_quota_key(request, provider_name="anthropic"),
    )
    async with SessionLocal() as session:
        session.add(
            StickySession(
                key=sticky_key,
                account_id="anthropic-stale",
                kind=StickySessionKind.CODEX_SESSION,
            )
        )
        await session.commit()

    async def fake_ensure_fresh(self, account, *, force=False):
        del self, force
        if account.id == "anthropic-stale":
            from app.modules.accounts.repository import AccountsRepository

            async with SessionLocal() as session:
                await AccountsRepository(session).update_status(
                    account.id, AccountStatus.REAUTH_REQUIRED, "Refresh token grant invalid - re-login required"
                )
            raise anthropic_proxy_module.RefreshError(
                "auth_refresh_invalid_grant",
                "Refresh token not found or invalid",
                False,
            )
        return account

    seen_authorizations: list[str] = []

    def fake_open_upstream_response(self, session, *, provider_name, headers, json_body):
        del self, session, provider_name, json_body
        seen_authorizations.append(headers["Authorization"])
        return _FakeResponseContext(_FakeResponse(200, ANTHROPIC_JSON_BYTES))

    monkeypatch.setattr(anthropic_proxy_module.AuthManager, "ensure_fresh", fake_ensure_fresh)
    monkeypatch.setattr(
        anthropic_proxy_module.AnthropicProxyService,
        "_open_upstream_response",
        fake_open_upstream_response,
    )

    response = await async_client.post(
        "/v1/messages",
        json=payload,
        headers={
            "anthropic-beta": "oauth-2025-04-20",
            "x-claude-session-id": "claude-refresh-failover",
        },
    )

    assert response.status_code == 200
    assert seen_authorizations == ["Bearer anthropic-access-healthy"]

    async with SessionLocal() as session:
        accounts = {
            account.id: account
            for account in (
                await session.execute(select(Account).where(Account.id.in_(["anthropic-stale", "anthropic-healthy"])))
            )
            .scalars()
            .all()
        }
        logs = list((await session.execute(select(RequestLog).order_by(RequestLog.id))).scalars())
        sticky = (
            await session.execute(
                select(StickySession).where(
                    StickySession.key == sticky_key,
                    StickySession.kind == StickySessionKind.CODEX_SESSION,
                )
            )
        ).scalar_one()

    assert accounts["anthropic-stale"].status == AccountStatus.REAUTH_REQUIRED
    assert accounts["anthropic-stale"].deactivation_reason == "Refresh token grant invalid - re-login required"
    assert accounts["anthropic-healthy"].status == AccountStatus.ACTIVE
    assert [log.status for log in logs] == ["error", "success"]
    assert [log.account_id for log in logs] == ["anthropic-stale", "anthropic-healthy"]
    assert logs[0].error_code == "auth_refresh_invalid_grant"
    assert sticky.account_id == "anthropic-healthy"


@pytest.mark.asyncio
async def test_anthropic_429_records_quota_cooldown_and_bounded_account_backoff_then_fails_over(
    async_client,
    monkeypatch,
):
    await _insert_account(
        account_id="anthropic-a",
        provider="anthropic",
        access_token="anthropic-access-a",
        email="claude-a@example.com",
    )
    await _insert_account(
        account_id="anthropic-b",
        provider="anthropic",
        access_token="anthropic-access-b",
        email="claude-b@example.com",
    )

    payload = {
        "model": "claude-fable-5",
        "max_tokens": 32,
        "messages": [{"role": "user", "content": "fail over high tier"}],
        "thinking": {"type": "adaptive"},
    }
    request = AnthropicMessageRequest.model_validate(payload)
    quota_key = anthropic_proxy_module._anthropic_quota_key(request)
    affinity_quota_key = anthropic_proxy_module._messages_affinity_quota_key(request, provider_name="anthropic")
    sticky_key = anthropic_proxy_module._anthropic_sticky_key(
        request,
        {"x-claude-session-id": "claude-session-failover"},
        quota_key=affinity_quota_key,
    )
    async with SessionLocal() as session:
        session.add(
            StickySession(
                key=sticky_key,
                account_id="anthropic-a",
                kind=StickySessionKind.CODEX_SESSION,
            )
        )
        await session.commit()

    seen_authorizations: list[str] = []
    cooldown_reset_at = int((utcnow() + timedelta(minutes=10)).replace(tzinfo=timezone.utc).timestamp())
    cooldown_reset_header = datetime.fromtimestamp(cooldown_reset_at, tz=timezone.utc).isoformat()

    def fake_open_upstream_response(self, session, *, provider_name, headers, json_body):
        del self, session, json_body
        assert provider_name == "anthropic"
        seen_authorizations.append(headers["Authorization"])
        if headers["Authorization"] == "Bearer anthropic-access-a":
            return _FakeResponseContext(
                _FakeResponse(
                    429,
                    b'{"error":{"message":"top model cooldown"}}',
                    headers={
                        "anthropic-ratelimit-unified-reset": cooldown_reset_header,
                    },
                )
            )
        return _FakeResponseContext(_FakeResponse(200, ANTHROPIC_JSON_BYTES))

    monkeypatch.setattr(
        anthropic_proxy_module.AnthropicProxyService,
        "_open_upstream_response",
        fake_open_upstream_response,
    )

    response = await async_client.post(
        "/v1/messages",
        json=payload,
        headers={
            "anthropic-beta": "oauth-2025-04-20",
            "x-claude-session-id": "claude-session-failover",
        },
    )

    assert response.status_code == 200
    assert seen_authorizations == ["Bearer anthropic-access-a", "Bearer anthropic-access-b"]

    async with SessionLocal() as session:
        accounts = {
            account.id: account
            for account in (
                await session.execute(select(Account).where(Account.id.in_(["anthropic-a", "anthropic-b"])))
            )
            .scalars()
            .all()
        }
        cooldowns = (
            (
                await session.execute(
                    select(AdditionalUsageHistory).where(
                        AdditionalUsageHistory.quota_key == quota_key,
                        AdditionalUsageHistory.window == "primary",
                    )
                )
            )
            .scalars()
            .all()
        )
        logs = list((await session.execute(select(RequestLog).order_by(RequestLog.id))).scalars())
        sticky = (
            await session.execute(
                select(StickySession).where(
                    StickySession.key == sticky_key,
                    StickySession.kind == StickySessionKind.CODEX_SESSION,
                )
            )
        ).scalar_one()

    assert accounts["anthropic-a"].status == AccountStatus.RATE_LIMITED
    assert accounts["anthropic-a"].blocked_at is not None
    assert 0 < accounts["anthropic-a"].reset_at - accounts["anthropic-a"].blocked_at <= 60
    assert accounts["anthropic-b"].status == AccountStatus.ACTIVE
    by_account = {entry.account_id: entry for entry in cooldowns}
    assert by_account["anthropic-a"].used_percent == 100.0
    assert by_account["anthropic-a"].reset_at == cooldown_reset_at
    assert by_account["anthropic-b"].used_percent == 0.0
    assert [log.status for log in logs] == ["error", "success"]
    assert [log.account_id for log in logs] == ["anthropic-a", "anthropic-b"]
    assert sticky.account_id == "anthropic-b"

    accounts_response = await async_client.get("/api/accounts")
    assert accounts_response.status_code == 200
    anthropic_a = next(
        account for account in accounts_response.json()["accounts"] if account["accountId"] == "anthropic-a"
    )
    cooldown = next(quota for quota in anthropic_a["additionalQuotas"] if quota["quotaKey"] == quota_key)
    assert cooldown["displayLabel"] == "Claude top models with thinking"
    assert cooldown["primaryWindow"]["usedPercent"] == 100.0


@pytest.mark.asyncio
async def test_anthropic_429_failover_repins_durable_mapping_to_serving_account(
    async_client,
    monkeypatch,
):

    await _insert_account(
        account_id="anthropic-a",
        provider="anthropic",
        access_token="anthropic-access-a",
        email="claude-a@example.com",
    )
    await _insert_account(
        account_id="anthropic-b",
        provider="anthropic",
        access_token="anthropic-access-b",
        email="claude-b@example.com",
    )

    payload = {
        "model": "claude-fable-5",
        "max_tokens": 32,
        "messages": [{"role": "user", "content": "repin to serving account"}],
        "thinking": {"type": "adaptive"},
    }
    request = AnthropicMessageRequest.model_validate(payload)
    quota_key = anthropic_proxy_module._messages_affinity_quota_key(request, provider_name="anthropic")
    sticky_key = anthropic_proxy_module._anthropic_sticky_key(
        request,
        {"x-claude-session-id": "claude-session-repin"},
        quota_key=quota_key,
    )
    async with SessionLocal() as session:
        session.add(
            StickySession(
                key=sticky_key,
                account_id="anthropic-a",
                kind=StickySessionKind.CODEX_SESSION,
            )
        )
        await session.commit()

    seen_authorizations: list[str] = []
    cooldown_reset_at = int((utcnow() + timedelta(minutes=10)).replace(tzinfo=timezone.utc).timestamp())
    cooldown_reset_header = datetime.fromtimestamp(cooldown_reset_at, tz=timezone.utc).isoformat()

    def fake_open_upstream_response(self, session, *, provider_name, headers, json_body):
        del self, session, provider_name, json_body
        seen_authorizations.append(headers["Authorization"])
        if headers["Authorization"] == "Bearer anthropic-access-a":
            return _FakeResponseContext(
                _FakeResponse(
                    429,
                    b'{"error":{"message":"top model cooldown"}}',
                    headers={"anthropic-ratelimit-unified-reset": cooldown_reset_header},
                )
            )
        return _FakeResponseContext(_FakeResponse(200, ANTHROPIC_JSON_BYTES))

    monkeypatch.setattr(
        anthropic_proxy_module.AnthropicProxyService,
        "_open_upstream_response",
        fake_open_upstream_response,
    )

    headers = {"anthropic-beta": "oauth-2025-04-20", "x-claude-session-id": "claude-session-repin"}
    first = await async_client.post("/v1/messages", json=payload, headers=headers)
    assert first.status_code == 200
    assert seen_authorizations == ["Bearer anthropic-access-a", "Bearer anthropic-access-b"]

    async def _durable_mapping() -> str:
        async with SessionLocal() as session:
            row = (
                await session.execute(
                    select(StickySession).where(
                        StickySession.key == sticky_key,
                        StickySession.kind == StickySessionKind.CODEX_SESSION,
                    )
                )
            ).scalar_one()
            return row.account_id

    assert await _durable_mapping() == "anthropic-b"

    second = await async_client.post("/v1/messages", json=payload, headers=headers)
    assert second.status_code == 200
    assert seen_authorizations == [
        "Bearer anthropic-access-a",
        "Bearer anthropic-access-b",
        "Bearer anthropic-access-b",
    ]
    assert await _durable_mapping() == "anthropic-b"
