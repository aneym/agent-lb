from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select

import app.modules.proxy.anthropic_service as anthropic_proxy_module
from app.core.anthropic.models import AnthropicMessageRequest
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, AdditionalUsageHistory, RequestLog, StickySession, StickySessionKind
from app.db.session import SessionLocal
from app.modules.proxy.load_balancer import AccountSelection

pytestmark = pytest.mark.integration


ANTHROPIC_SSE_BYTES = (
    b'event: message_start\ndata: {"type":"message_start","message":{"id":"msg_123","type":"message",'
    b'"role":"assistant","model":"claude-sonnet-4-20250514","content":[],"usage":{"input_tokens":10,'
    b'"cache_creation_input_tokens":3,"cache_read_input_tokens":4}}}\n\n'
    b'event: content_block_start\ndata: {"type":"content_block_start","index":0,'
    b'"content_block":{"type":"text","text":""}}\n\n'
    b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,'
    b'"delta":{"type":"text_delta","text":"ok"}}\n\n'
    b'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn"},'
    b'"usage":{"output_tokens":5}}\n\n'
    b'event: message_stop\ndata: {"type":"message_stop"}\n\n'
)


@pytest.mark.asyncio
async def test_anthropic_messages_returns_error_when_no_accounts_available(async_client):
    response = await async_client.post(
        "/v1/messages",
        json={
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 32,
            "messages": [{"role": "user", "content": "hello"}],
        },
        headers={"anthropic-beta": "oauth-2025-04-20"},
    )

    assert response.status_code == 503
    assert response.json() == {
        "type": "error",
        "error": {
            "type": "no_available_anthropic_accounts",
            "message": (
                "No available accounts. Service is operating in degraded mode: "
                "all upstream accounts are unavailable"
            ),
        },
    }


class _FakeContent:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def iter_chunked(self, _: int) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk


class _FakeResponse:
    def __init__(self, status: int, body: bytes, headers: dict[str, str] | None = None) -> None:
        self.status = status
        self.content = _FakeContent([body])
        self._body = body
        self.headers = headers or {}

    async def read(self) -> bytes:
        return self._body


class _FakeResponseContext:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response

    async def __aenter__(self) -> _FakeResponse:
        return self._response

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


def test_anthropic_rate_limit_error_parses_unified_reset_headers():
    response = _FakeResponse(
        429,
        b'{"error":{"message":"rate limited"}}',
        headers={
            "anthropic-ratelimit-unified-reset": "2026-06-09T19:50:00.000000+00:00",
            "retry-after": "37",
        },
    )

    error = anthropic_proxy_module._rate_limit_error_from_response(response, "rate limited")

    assert error["resets_at"] == 1781034600
    assert error["resets_in_seconds"] == 37


def test_anthropic_rate_limit_error_parses_epoch_reset_headers():
    response = _FakeResponse(
        429,
        b'{"error":{"message":"rate limited"}}',
        headers={"anthropic-ratelimit-unified-reset": "1781034600"},
    )

    error = anthropic_proxy_module._rate_limit_error_from_response(response, "rate limited")

    assert error["resets_at"] == 1781034600


def test_anthropic_sticky_key_uses_quota_scoped_session_hash():
    payload = AnthropicMessageRequest.model_validate(
        {
            "model": "claude-fable-5",
            "max_tokens": 32,
            "messages": [{"role": "user", "content": "hello"}],
            "thinking": {"type": "adaptive"},
        }
    )

    key = anthropic_proxy_module._anthropic_sticky_key(
        payload,
        {"x-claude-session-id": "session-123"},
        quota_key=anthropic_proxy_module._anthropic_quota_key(payload),
    )

    assert key is not None
    assert key.startswith("claude:anthropic_top_thinking:session:")
    assert "session-123" not in key


async def _insert_account(
    *,
    account_id: str,
    provider: str,
    access_token: str,
    email: str,
    status: AccountStatus = AccountStatus.ACTIVE,
) -> None:
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        session.add(
            Account(
                id=account_id,
                provider=provider,
                chatgpt_account_id=f"workspace-{account_id}" if provider == "openai" else account_id,
                email=email,
                plan_type="max" if provider == "anthropic" else "plus",
                access_token_encrypted=encryptor.encrypt(access_token),
                refresh_token_encrypted=encryptor.encrypt(f"refresh-{account_id}"),
                id_token_encrypted=encryptor.encrypt(f"id-{account_id}") if provider == "openai" else None,
                last_refresh=utcnow() + timedelta(days=1),
                status=status,
                deactivation_reason=None,
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_anthropic_session_route_surfaces_provider_status_failure(async_client):
    for index in range(3):
        await _insert_account(
            account_id=f"anthropic-rate-limited-{index}",
            provider="anthropic",
            access_token=f"anthropic-access-{index}",
            email=f"claude-{index}@example.com",
            status=AccountStatus.RATE_LIMITED,
        )

    response = await async_client.post(
        "/api/anthropic/session-route",
        json={
            "sessionId": "session-route-rate-limited-accounts",
            "model": "claude-fable-5",
            "quotaKey": "anthropic_top_thinking",
        },
    )

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "message": (
                "3 Anthropic accounts exist, but none are selectable for "
                "claude-fable-5/anthropic_top_thinking; statuses: rate_limited=3. "
                "OpenAI accounts are not eligible for Claude routing."
            ),
            "type": "server_error",
            "code": "no_available_anthropic_accounts",
        }
    }


async def _insert_quota_cooldown(*, account_id: str, quota_key: str, reset_at: int) -> None:
    async with SessionLocal() as session:
        session.add(
            AdditionalUsageHistory(
                account_id=account_id,
                quota_key=quota_key,
                limit_name=quota_key,
                metered_feature="anthropic_messages",
                window="primary",
                used_percent=100.0,
                reset_at=reset_at,
                window_minutes=10,
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_anthropic_messages_quota_cooldown_returns_native_rate_limit(async_client):
    await _insert_account(
        account_id="anthropic-cooling",
        provider="anthropic",
        access_token="anthropic-access",
        email="claude@example.com",
    )
    reset_at = int((utcnow() + timedelta(minutes=7)).replace(tzinfo=timezone.utc).timestamp())
    await _insert_quota_cooldown(
        account_id="anthropic-cooling",
        quota_key="anthropic_top_thinking",
        reset_at=reset_at,
    )

    response = await async_client.post(
        "/v1/messages",
        json={
            "model": "claude-fable-5",
            "max_tokens": 32,
            "messages": [{"role": "user", "content": "hello"}],
            "thinking": {"type": "adaptive"},
        },
        headers={"anthropic-beta": "oauth-2025-04-20"},
    )

    assert response.status_code == 429
    body = response.json()
    assert body["type"] == "error"
    assert body["error"]["type"] == "rate_limit_error"
    assert "cooling down" in body["error"]["message"]
    assert response.headers["anthropic-ratelimit-unified-reset"] == str(reset_at)
    assert 0 < int(response.headers["retry-after"]) <= 7 * 60


@pytest.mark.asyncio
async def test_anthropic_session_route_includes_retry_metadata(async_client):
    await _insert_account(
        account_id="anthropic-cooling",
        provider="anthropic",
        access_token="anthropic-access",
        email="claude@example.com",
    )
    reset_at = int((utcnow() + timedelta(minutes=7)).replace(tzinfo=timezone.utc).timestamp())
    await _insert_quota_cooldown(
        account_id="anthropic-cooling",
        quota_key="anthropic_top_thinking",
        reset_at=reset_at,
    )

    response = await async_client.post(
        "/api/anthropic/session-route",
        json={
            "sessionId": "session-route-cooldown",
            "model": "claude-fable-5",
            "quotaKey": "anthropic_top_thinking",
        },
    )

    assert response.status_code == 429
    error = response.json()["error"]
    assert error["code"] == "anthropic_quota_cooldown"
    assert error["retryAt"] == datetime.fromtimestamp(reset_at, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    assert 0 < error["retryAfterSeconds"] <= 7 * 60
    assert response.headers["retry-after"] == str(error["retryAfterSeconds"])


@pytest.mark.asyncio
async def test_anthropic_messages_mid_stream_failure_emits_sse_error_event(async_client, monkeypatch):
    await _insert_account(
        account_id="anthropic-only",
        provider="anthropic",
        access_token="anthropic-access",
        email="claude@example.com",
    )
    reset_at = int((utcnow() + timedelta(minutes=9)).replace(tzinfo=timezone.utc).timestamp())

    def fake_open_upstream_response(self, session, *, headers, json_body):
        del self, session, headers, json_body
        return _FakeResponseContext(
            _FakeResponse(
                429,
                b'{"error":{"message":"top model cooldown"}}',
                headers={"anthropic-ratelimit-unified-reset": str(reset_at)},
            )
        )

    monkeypatch.setattr(
        anthropic_proxy_module.AnthropicProxyService,
        "_open_upstream_response",
        fake_open_upstream_response,
    )

    async with async_client.stream(
        "POST",
        "/v1/messages",
        json={
            "model": "claude-fable-5",
            "max_tokens": 32,
            "stream": True,
            "messages": [{"role": "user", "content": "hello"}],
            "thinking": {"type": "adaptive"},
        },
        headers={"anthropic-beta": "oauth-2025-04-20"},
    ) as response:
        assert response.status_code == 200
        body = await response.aread()

    text = body.decode("utf-8")
    assert "event: error\n" in text
    error_data = json.loads(text.split("event: error\ndata: ", 1)[1].split("\n\n", 1)[0])
    assert error_data["type"] == "error"
    assert error_data["error"]["type"] == "rate_limit_error"


@pytest.mark.asyncio
async def test_anthropic_messages_streams_sse_and_logs_usage(async_client, monkeypatch):
    await _insert_account(
        account_id="openai-account",
        provider="openai",
        access_token="openai-access",
        email="openai@example.com",
    )
    await _insert_account(
        account_id="anthropic-account",
        provider="anthropic",
        access_token="anthropic-access",
        email="claude@example.com",
    )

    captured: dict[str, Any] = {}

    def fake_open_upstream_response(self, session, *, headers, json_body):
        del self, session
        captured["headers"] = dict(headers)
        captured["json_body"] = dict(json_body)
        return _FakeResponseContext(_FakeResponse(200, ANTHROPIC_SSE_BYTES))

    monkeypatch.setattr(
        anthropic_proxy_module.AnthropicProxyService,
        "_open_upstream_response",
        fake_open_upstream_response,
    )

    payload = {
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 32,
        "stream": True,
        "system": "Claude Code system prompt prefix must pass through untouched.",
        "messages": [
            {"role": "user", "content": "hello"},
            {"role": "system", "content": "Runtime system context injected by Claude Code."},
        ],
        "thinking": {"type": "adaptive"},
        "context_management": {"edits": [{"type": "clear_tool_uses_20250919"}]},
        "output_config": {"container": {"type": "auto"}},
    }
    async with async_client.stream(
        "POST",
        "/v1/messages",
        json=payload,
        headers={
            "anthropic-beta": "oauth-2025-04-20",
            "authorization": "Bearer client-token",
            "x-api-key": "client-placeholder",
        },
    ) as response:
        assert response.status_code == 200
        body = await response.aread()

    assert body == ANTHROPIC_SSE_BYTES
    assert captured["headers"]["Authorization"] == "Bearer anthropic-access"
    assert captured["headers"]["anthropic-beta"] == "oauth-2025-04-20"
    assert captured["headers"]["anthropic-version"] == "2023-06-01"
    assert "x-api-key" not in {key.lower() for key in captured["headers"]}
    assert captured["json_body"]["system"] == payload["system"]
    assert captured["json_body"]["messages"] == payload["messages"]
    assert captured["json_body"]["thinking"] == payload["thinking"]
    assert captured["json_body"]["context_management"] == payload["context_management"]
    assert captured["json_body"]["output_config"] == payload["output_config"]

    async with SessionLocal() as session:
        result = await session.execute(select(RequestLog))
        logs = list(result.scalars())

    assert len(logs) == 1
    log = logs[0]
    assert log.provider == "anthropic"
    assert log.account_id == "anthropic-account"
    assert log.status == "success"
    assert log.input_tokens == 10
    assert log.output_tokens == 5
    assert log.cache_creation_tokens == 3
    assert log.cache_read_tokens == 4
    assert log.cost_usd is not None


ANTHROPIC_JSON_BYTES = (
    b'{"id":"msg_456","type":"message","role":"assistant","model":"claude-sonnet-4-20250514",'
    b'"content":[{"type":"text","text":"ok"}],"stop_reason":"end_turn",'
    b'"usage":{"input_tokens":12,"output_tokens":7,"cache_creation_input_tokens":2,"cache_read_input_tokens":1}}'
)


@pytest.mark.asyncio
async def test_anthropic_messages_non_streaming_logs_usage(async_client, monkeypatch):
    await _insert_account(
        account_id="anthropic-account",
        provider="anthropic",
        access_token="anthropic-access",
        email="claude@example.com",
    )

    def fake_open_upstream_response(self, session, *, headers, json_body):
        del self, session, headers, json_body
        return _FakeResponseContext(_FakeResponse(200, ANTHROPIC_JSON_BYTES))

    monkeypatch.setattr(
        anthropic_proxy_module.AnthropicProxyService,
        "_open_upstream_response",
        fake_open_upstream_response,
    )

    response = await async_client.post(
        "/v1/messages",
        json={
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 32,
            "messages": [{"role": "user", "content": "hello"}],
        },
        headers={"anthropic-beta": "oauth-2025-04-20", "x-api-key": "client-placeholder"},
    )
    assert response.status_code == 200
    assert response.content == ANTHROPIC_JSON_BYTES

    async with SessionLocal() as session:
        result = await session.execute(select(RequestLog))
        logs = list(result.scalars())

    assert len(logs) == 1
    log = logs[0]
    assert log.provider == "anthropic"
    assert log.status == "success"
    assert log.input_tokens == 12
    assert log.output_tokens == 7
    assert log.cache_creation_tokens == 2
    assert log.cache_read_tokens == 1
    assert log.cost_usd is not None


@pytest.mark.asyncio
async def test_anthropic_messages_non_streaming_upstream_529_returns_native_error(async_client, monkeypatch):
    await _insert_account(
        account_id="anthropic-overloaded",
        provider="anthropic",
        access_token="anthropic-access",
        email="claude@example.com",
    )

    def fake_open_upstream_response(self, session, *, headers, json_body):
        del self, session, headers, json_body
        return _FakeResponseContext(
            _FakeResponse(
                529,
                b'{"type":"error","error":{"type":"overloaded_error","message":"Overloaded"}}',
            )
        )

    monkeypatch.setattr(
        anthropic_proxy_module.AnthropicProxyService,
        "_open_upstream_response",
        fake_open_upstream_response,
    )

    response = await async_client.post(
        "/v1/messages",
        json={
            "model": "claude-fable-5",
            "max_tokens": 32,
            "messages": [{"role": "user", "content": "hello"}],
        },
        headers={"anthropic-beta": "oauth-2025-04-20"},
    )

    assert response.status_code == 529
    assert response.json() == {
        "type": "error",
        "error": {"type": "overloaded_error", "message": "Overloaded"},
    }

    async with SessionLocal() as session:
        log = (await session.execute(select(RequestLog))).scalar_one()

    assert log.status == "error"
    assert log.error_code == "upstream_529"
    assert log.error_message == "Overloaded"


@pytest.mark.asyncio
async def test_anthropic_messages_passes_primary_reset_preference_to_selector(async_client, monkeypatch):
    await _insert_account(
        account_id="anthropic-primary-reset",
        provider="anthropic",
        access_token="anthropic-access",
        email="claude@example.com",
    )

    class _SettingsCache:
        async def get(self) -> SimpleNamespace:
            return SimpleNamespace(prefer_earlier_reset_accounts=True)

    seen_selection_kwargs: list[dict[str, Any]] = []

    async def fake_select_account(self, **kwargs):
        del self
        seen_selection_kwargs.append(kwargs)
        async with SessionLocal() as session:
            account = await session.get(Account, "anthropic-primary-reset")
        return AccountSelection(account=account, error_message=None)

    def fake_open_upstream_response(self, session, *, headers, json_body):
        del self, session, headers, json_body
        return _FakeResponseContext(_FakeResponse(200, ANTHROPIC_JSON_BYTES))

    monkeypatch.setattr(anthropic_proxy_module, "get_settings_cache", lambda: _SettingsCache())
    monkeypatch.setattr(anthropic_proxy_module.LoadBalancer, "select_account", fake_select_account)
    monkeypatch.setattr(
        anthropic_proxy_module.AnthropicProxyService,
        "_open_upstream_response",
        fake_open_upstream_response,
    )

    response = await async_client.post(
        "/v1/messages",
        json={
            "model": "claude-fable-5",
            "max_tokens": 32,
            "messages": [{"role": "user", "content": "hello"}],
            "thinking": {"type": "adaptive"},
        },
        headers={"anthropic-beta": "oauth-2025-04-20"},
    )

    assert response.status_code == 200
    assert seen_selection_kwargs[0]["prefer_earlier_reset_accounts"] is True
    assert seen_selection_kwargs[0]["prefer_earlier_reset_window"] == "primary"
    assert seen_selection_kwargs[0]["routing_strategy"] == "usage_weighted"


@pytest.mark.asyncio
async def test_anthropic_messages_keep_same_session_on_sticky_account(async_client, monkeypatch):
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

    seen_authorizations: list[str] = []

    def fake_open_upstream_response(self, session, *, headers, json_body):
        del self, session, json_body
        seen_authorizations.append(headers["Authorization"])
        return _FakeResponseContext(_FakeResponse(200, ANTHROPIC_JSON_BYTES))

    monkeypatch.setattr(
        anthropic_proxy_module.AnthropicProxyService,
        "_open_upstream_response",
        fake_open_upstream_response,
    )

    payload = {
        "model": "claude-fable-5",
        "max_tokens": 32,
        "messages": [{"role": "user", "content": "keep me sticky"}],
        "thinking": {"type": "adaptive"},
    }
    for _ in range(2):
        response = await async_client.post(
            "/v1/messages",
            json=payload,
            headers={
                "anthropic-beta": "oauth-2025-04-20",
                "x-claude-session-id": "claude-session-sticky",
            },
        )
        assert response.status_code == 200

    assert len(seen_authorizations) == 2
    assert seen_authorizations[1] == seen_authorizations[0]

    request = AnthropicMessageRequest.model_validate(payload)
    sticky_key = anthropic_proxy_module._anthropic_sticky_key(
        request,
        {"x-claude-session-id": "claude-session-sticky"},
        quota_key=anthropic_proxy_module._anthropic_quota_key(request),
    )
    async with SessionLocal() as session:
        result = await session.execute(
            select(StickySession).where(
                StickySession.key == sticky_key,
                StickySession.kind == StickySessionKind.CODEX_SESSION,
            )
        )
        sticky = result.scalar_one()

    expected_account_id = "anthropic-a" if seen_authorizations[0].endswith("anthropic-access-a") else "anthropic-b"
    assert sticky.account_id == expected_account_id


@pytest.mark.asyncio
async def test_anthropic_429_records_quota_cooldown_and_fails_over_without_global_rate_limit(
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
    sticky_key = anthropic_proxy_module._anthropic_sticky_key(
        request,
        {"x-claude-session-id": "claude-session-failover"},
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

    def fake_open_upstream_response(self, session, *, headers, json_body):
        del self, session, json_body
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

    assert accounts["anthropic-a"].status == AccountStatus.ACTIVE
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
