from __future__ import annotations

import json
from typing import Any, cast

import aiohttp
import pytest

from app.core.anthropic.oauth import (
    ANTHROPIC_OAUTH_BETA,
    exchange_anthropic_authorization_code,
    refresh_anthropic_access_token,
)
from app.core.auth.refresh import RefreshError

pytestmark = pytest.mark.unit


class _FakeResponse:
    def __init__(self, *, status: int, payload: dict[str, Any]) -> None:
        self.status = status
        self._payload = payload

    async def __aenter__(self) -> "_FakeResponse":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def json(self, *, content_type: str | None = None) -> dict[str, Any]:
        return self._payload

    async def text(self) -> str:
        return json.dumps(self._payload)


class _FakeSession:
    def __init__(self, response: _FakeResponse) -> None:
        self.response = response
        self.requests: list[dict[str, Any]] = []

    def post(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.requests.append({"url": url, **kwargs})
        return self.response


@pytest.mark.asyncio
async def test_exchange_anthropic_authorization_code_parses_token_metadata_without_id_token() -> None:
    session = _FakeSession(
        _FakeResponse(
            status=200,
            payload={
                "access_token": "anthropic-access",
                "refresh_token": "anthropic-refresh",
                "token_type": "Bearer",
                "expires_in": 3600,
                "profile": {"email": "claude@example.com"},
                "user": {"id": "user_123"},
                "plan_type": "max",
            },
        )
    )

    tokens = await exchange_anthropic_authorization_code(
        code="code-123",
        code_verifier="verifier-456",
        state="state-789",
        redirect_uri="http://localhost:1455/auth/callback",
        token_url="https://platform.claude.com/v1/oauth/token",
        client_id="client-id",
        session=cast(aiohttp.ClientSession, session),
    )

    assert tokens.access_token == "anthropic-access"
    assert tokens.refresh_token == "anthropic-refresh"
    assert tokens.id_token is None
    assert tokens.account_id == "user_123"
    assert tokens.email == "claude@example.com"
    assert tokens.plan_type == "max"
    assert session.requests[0]["url"] == "https://platform.claude.com/v1/oauth/token"
    assert session.requests[0]["headers"]["anthropic-beta"] == ANTHROPIC_OAUTH_BETA
    assert session.requests[0]["headers"]["Content-Type"] == "application/json"
    body = json.loads(session.requests[0]["data"])
    assert body["grant_type"] == "authorization_code"
    assert body["code"] == "code-123"
    assert body["code_verifier"] == "verifier-456"
    assert body["state"] == "state-789"


@pytest.mark.asyncio
async def test_exchange_anthropic_authorization_code_reads_account_email_address() -> None:
    session = _FakeSession(
        _FakeResponse(
            status=200,
            payload={
                "access_token": "anthropic-access",
                "refresh_token": "anthropic-refresh",
                "token_type": "Bearer",
                "account": {
                    "id": "account_789",
                    "email_address": "account-email@example.com",
                    "plan_type": "max",
                },
            },
        )
    )

    tokens = await exchange_anthropic_authorization_code(
        code="code-123",
        code_verifier="verifier-456",
        token_url="https://platform.claude.com/v1/oauth/token",
        client_id="client-id",
        session=cast(aiohttp.ClientSession, session),
    )

    assert tokens.account_id == "account_789"
    assert tokens.email == "account-email@example.com"
    assert tokens.plan_type == "max"


@pytest.mark.asyncio
async def test_refresh_anthropic_access_token_returns_optional_id_token() -> None:
    session = _FakeSession(
        _FakeResponse(
            status=200,
            payload={
                "access_token": "refreshed-access",
                "refresh_token": "refreshed-refresh",
                "account_id": "account_456",
                "email": "renewed@example.com",
            },
        )
    )

    result = await refresh_anthropic_access_token(
        "old-refresh",
        token_url="https://platform.claude.com/v1/oauth/token",
        client_id="client-id",
        session=cast(aiohttp.ClientSession, session),
    )

    assert result.access_token == "refreshed-access"
    assert result.refresh_token == "refreshed-refresh"
    assert result.id_token is None
    assert result.account_id == "account_456"
    assert result.email == "renewed@example.com"
    assert result.plan_type == "claude"
    body = json.loads(session.requests[0]["data"])
    assert body["grant_type"] == "refresh_token"
    assert body["refresh_token"] == "old-refresh"
    assert "scope" not in body


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {
            "error": "invalid_grant",
            "error_description": "Refresh token not found or invalid",
        },
        {
            "error": {
                "code": "invalid_grant",
                "message": "Refresh token not found or invalid",
            },
        },
    ],
)
async def test_refresh_anthropic_access_token_marks_invalid_grant_permanent(
    payload: dict[str, Any],
) -> None:
    session = _FakeSession(_FakeResponse(status=400, payload=payload))

    with pytest.raises(RefreshError) as exc_info:
        await refresh_anthropic_access_token(
            "old-refresh",
            token_url="https://platform.claude.com/v1/oauth/token",
            client_id="client-id",
            session=cast(aiohttp.ClientSession, session),
        )

    assert exc_info.value.code == "invalid_grant"
    assert exc_info.value.message == "Refresh token not found or invalid"
    assert exc_info.value.is_permanent is True
