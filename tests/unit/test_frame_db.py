

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.auth.dependencies import validate_proxy_api_key
from app.core.middleware.api_firewall import _is_protected_api_path
from app.modules.proxy import anthropic_service, api
from app.modules.proxy.anthropic_service import AnthropicProxyService

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [200, 401, 429, 302])
async def test_frame_route_uses_pool_bearer_and_fixed_upstream(monkeypatch, status):
    service = object.__new__(AnthropicProxyService)
    account = SimpleNamespace(id="fixture-account")
    service._select_account = AsyncMock(return_value=account)
    service._fresh_access_token = AsyncMock(side_effect=["fixture-pool-token", "fixture-refreshed-token"])
    service._persist_request_log = AsyncMock()
    calls = []

    class Session:
        @asynccontextmanager
        async def request(self, method, url, **kwargs):
            calls.append((method, url, kwargs))
            yield SimpleNamespace(
                status=status,
                headers={"content-type": "application/json"},
                read=AsyncMock(return_value=b'{"fixture":true}'),
            )

    @asynccontextmanager
    async def lease():
        yield Session()

    monkeypatch.setattr(anthropic_service, "lease_http_session", lease)
    app = FastAPI()
    app.include_router(api.frame_db_router)
    app.dependency_overrides[validate_proxy_api_key] = lambda: None
    app.dependency_overrides[api.get_anthropic_proxy_context] = lambda: SimpleNamespace(service=service)
    raw_body = b'{ "slug": "fixture", "verb": "list", "args": {"collection": "items"} }'
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost") as client:
        response = await client.post(
            "/api/frame/db/agent",
            content=raw_body,
            headers={
                "content-type": "application/json",
                "x-agent-lb-seat": "fixture-seat",
                "Authorization": "Bearer fixture-placeholder",
                "X-Frame-CP": "go",
                "X-Frame-Surface": "code",
                "X-Frame-Platform": "cli",
                "X-Frame-Client-Version": "2.1.296",
                "x-claude-session-id": "fixture-session",
            },
        )
        missing = await client.post("/api/not-frame/db/agent", content=raw_body)
    assert response.status_code == status
    assert response.json() == {"fixture": True}
    assert missing.status_code == 404
    method, url, kwargs = calls[0]
    assert method == "POST"
    assert str(url) == "https://api.anthropic.com/api/frame/db/agent"
    assert kwargs["data"] == raw_body
    assert "json" not in kwargs
    assert kwargs["allow_redirects"] is False
    assert kwargs["headers"]["Authorization"] == "Bearer fixture-pool-token"
    assert "fixture-placeholder" not in kwargs["headers"].values()
    assert kwargs["headers"]["x-frame-cp"] == "go"
    assert kwargs["headers"]["x-frame-surface"] == "code"
    assert kwargs["headers"]["x-frame-platform"] == "cli"
    assert kwargs["headers"]["x-frame-client-version"] == "2.1.296"
    assert kwargs["headers"]["x-claude-session-id"] == "fixture-session"
    assert kwargs["headers"]["x-agent-lb-seat"] == "fixture-seat"
    service._select_account.assert_awaited_once()
    assert service._select_account.await_args.kwargs["provider_name"] == "anthropic"
    assert service._select_account.await_args.kwargs["sticky_key"] is not None
    if status == 401:
        assert len(calls) == 2
        retry_method, retry_url, retry_kwargs = calls[1]
        assert (retry_method, retry_url) == (method, url)
        assert retry_kwargs["data"] == raw_body
        assert retry_kwargs["allow_redirects"] is False
        assert retry_kwargs["headers"]["Authorization"] == "Bearer fixture-refreshed-token"
        assert retry_kwargs["headers"]["x-claude-session-id"] == "fixture-session"
        assert retry_kwargs["headers"]["x-agent-lb-seat"] == "fixture-seat"
        assert service._fresh_access_token.await_count == 2
        assert service._fresh_access_token.await_args_list[0].args == (account,)
        service._fresh_access_token.assert_awaited_with(account, rejected_access_token="fixture-pool-token")
    else:
        assert len(calls) == 1
        service._fresh_access_token.assert_awaited_once_with(account)
    service._persist_request_log.assert_awaited_once()
    assert service._persist_request_log.await_args.kwargs["request_kind"] == "frame"
    assert service._persist_request_log.await_args.kwargs["account"] is account


@pytest.mark.asyncio
async def test_frame_network_error_has_safe_response(monkeypatch):
    service = object.__new__(AnthropicProxyService)
    service._select_account = AsyncMock(return_value=SimpleNamespace(id="fixture"))
    service._fresh_access_token = AsyncMock(return_value="fixture-token")
    service._persist_request_log = AsyncMock()

    @asynccontextmanager
    async def lease():
        raise aiohttp.ClientError("sensitive upstream details")
        yield

    monkeypatch.setattr(anthropic_service, "lease_http_session", lease)
    app = FastAPI()
    app.include_router(api.frame_db_router)
    app.dependency_overrides[validate_proxy_api_key] = lambda: None
    app.dependency_overrides[api.get_anthropic_proxy_context] = lambda: SimpleNamespace(service=service)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost") as client:
        response = await client.post("/api/frame/db/agent", json={})
    assert response.status_code == 502
    assert "sensitive upstream details" not in response.text
    assert "fixture-token" not in response.text


@pytest.mark.parametrize(
    "path,expected",
    [
        ("/api/frame/db/agent", True),
        ("/api/frame/db", True),
        ("/api/frame/db-other/agent", True),
        ("/v1/messages", True),
    ],
)
def test_frame_firewall_scope(path, expected):
    assert _is_protected_api_path(path) is expected
