from __future__ import annotations

import json
import time
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

import app.modules.proxy.anthropic_service as anthropic_proxy_module
from app.db.models import Account, AccountStatus, RequestLog
from app.db.session import SessionLocal
from app.dependencies import get_anthropic_proxy_service_for_app
from app.modules.proxy.load_balancer import RuntimeState
from tests.integration.anthropic_proxy_support import (
    ANTHROPIC_SSE_BYTES,
    _FakeResponse,
    _FakeResponseContext,
    _insert_account,
    _insert_quota_cooldown,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
@pytest.mark.parametrize("runtime_delay, tripwire", [(30, False), (120, False), (900, True)])
async def test_session_route_retry_uses_earliest_live_guard_across_disjoint_accounts(
    async_client, app_instance, runtime_delay, tripwire
):
    now = int(time.time())
    for account_id in ("scope-cooling", "runtime-cooling"):
        await _insert_account(
            account_id=account_id,
            provider="anthropic",
            access_token="fixture-access",
            email=f"{account_id}@example.test",
        )
    await _insert_quota_cooldown(
        account_id="scope-cooling",
        quota_key=anthropic_proxy_module._ANTHROPIC_EXTRA_USAGE_QUOTA_KEY if tripwire else "anthropic_opus",
        reset_at=now + 120 if tripwire else now + 3600,
        recorded_at=datetime.fromtimestamp(now, tz=timezone.utc).replace(tzinfo=None),
    )
    service = get_anthropic_proxy_service_for_app(app_instance)
    service._load_balancer._runtime["runtime-cooling"] = RuntimeState(cooldown_until=now + runtime_delay)

    response = await async_client.post(
        "/api/anthropic/session-route",
        json={
            "sessionId": "mixed-live-guards",
            "model": "claude-opus-5",
            "quotaKey": "anthropic_top",
        },
    )

    assert response.status_code == 503
    error = response.json()["error"]
    expected_retry = now + min(120 if tripwire else 60, runtime_delay)
    assert error["retryAt"] == (
        datetime.fromtimestamp(expected_retry, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    )
    if not tripwire:
        assert "anthropic_opus cooldown excluded 1 account" in error["message"]
    assert "runtime cooldown excluded 1 account" in error["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("agent_id", "enabled", "expected_ttl"),
    [("helper-123", True, "1h"), (None, True, None), ("helper-123", False, None)],
)
async def test_helper_cache_ttl_at_upstream_boundary(async_client, monkeypatch, agent_id, enabled, expected_ttl):

    monkeypatch.setenv("AGENT_LB_ANTHROPIC_HELPER_CACHE_1H_ENABLED", str(enabled).lower())
    anthropic_proxy_module.get_settings.cache_clear()
    await _insert_account(
        account_id="helper-cache-account",
        provider="anthropic",
        access_token="helper-cache-test-access",
        email="cache@example.com",
    )
    forwarded = []

    def upstream(self, session, *, provider_name, headers, json_body):
        forwarded.append((headers, json_body))
        return _FakeResponseContext(_FakeResponse(200, ANTHROPIC_SSE_BYTES))

    monkeypatch.setattr(anthropic_proxy_module.AnthropicProxyService, "_open_upstream_response", upstream)
    request = {
        "model": "claude-sonnet-5",
        "max_tokens": 32,
        "stream": True,
        "system": [
            {"type": "text", "text": "x-anthropic-billing-header: cc_version=2.1.282; cch=probe;"},
            {"type": "text", "text": "You are Claude Code, Anthropic's official CLI for Claude."},
            {"type": "text", "text": "Stable system", "cache_control": {"type": "ephemeral"}},
        ],
        "tools": [
            {"name": "probe", "input_schema": {"type": "object"}, "cache_control": {"type": "ephemeral", "ttl": "5m"}}
        ],
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "reply ok", "cache_control": {"type": "ephemeral", "ttl": "1h"}},
                ],
            }
        ],
    }
    try:
        response = await async_client.post(
            "/v1/messages",
            json=request,
            headers={"x-claude-code-agent-id": agent_id} if agent_id else {},
        )
        assert response.status_code == 200
        headers, body = forwarded[0]
        expected = json.loads(json.dumps(request))
        if expected_ttl:
            for block in (expected["system"][2], expected["tools"][0], expected["messages"][0]["content"][0]):
                block["cache_control"]["ttl"] = expected_ttl
            assert "extended-cache-ttl-2025-04-11" in headers["anthropic-beta"]
        else:
            assert "extended-cache-ttl-2025-04-11" not in headers["anthropic-beta"]
        assert json.dumps(body, sort_keys=True) == json.dumps(expected, sort_keys=True)
        async with SessionLocal() as session:
            log = (await session.execute(select(RequestLog))).scalar_one()
        assert log.cache_creation_tokens == 3
        assert log.cache_creation_tier == expected_ttl
    finally:
        anthropic_proxy_module.get_settings.cache_clear()


@pytest.mark.parametrize("method", ["GET", "POST", "PUT", "PATCH", "DELETE"])
@pytest.mark.parametrize("auth_case", ["accepted", "recovered", "rejected"])
async def test_frame_database_proxy(async_client, monkeypatch, method, auth_case):
    monkeypatch.setenv("AGENT_LB_ANTHROPIC_FRAME_ACCOUNT_ID", "frame-account")

    from aiohttp import web

    await _insert_account(
        account_id="frame-account", provider="anthropic", access_token="frame-old", email="frame@example.com"
    )
    seen = []
    refreshes = []
    request_body = b'{ "value": "raw", "unicode": "\\u00e9" }\n'
    response_body = b'{ "shared": [1, 2], "unicode": "\\u00e9" }\n'

    async def frame(request):
        seen.append((request.method, request.raw_path, request.headers, await request.read()))
        status = 401 if auth_case == "rejected" or (auth_case == "recovered" and len(seen) == 1) else 200
        return web.Response(body=response_body, status=status, headers={"Content-Type": "application/json"})

    async def refresh(request):
        refreshes.append(await request.json())
        return web.json_response(
            {"access_token": "frame-new", "refresh_token": "frame-refresh-new", "expires_in": 3600}
        )

    upstream = web.Application()
    upstream.router.add_route("*", "/api/frame/{path:.*}", frame)
    upstream.router.add_post("/token", refresh)
    runner = web.AppRunner(upstream)
    await runner.setup()
    try:
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        host, port = runner.addresses[0]
        base = f"http://{host}:{port}"
        monkeypatch.setenv("AGENT_LB_ANTHROPIC_UPSTREAM_BASE_URL", base)
        monkeypatch.setenv("AGENT_LB_ANTHROPIC_OAUTH_TOKEN_URL", f"{base}/token")
        from app.core.config.settings import get_settings

        get_settings.cache_clear()
        headers = {"x-claude-session-id": "frame-session", "x-agent-lb-seat": "raise-desk-watch"}
        claim = await async_client.post("/api/anthropic/session-route", json={"sessionId": "frame-session"})
        assert claim.status_code == 200
        assert claim.json()["accountId"] == "frame-account"
        await _insert_account(
            account_id="frame-other", provider="anthropic", access_token="other-token", email="other@example.com"
        )
        response = await async_client.request(
            method, "/api/frame/db/agent?artifact=a%2Fb&key=x&key=y", content=request_body, headers=headers
        )
        assert response.status_code == (401 if auth_case == "rejected" else 200)
        assert response.content == response_body
        assert response.headers["content-type"] == "application/json"
        assert len(seen) == (1 if auth_case == "accepted" else 2)
        for index, (seen_method, path, forwarded, body) in enumerate(seen):
            assert seen_method == method
            assert path == "/api/frame/db/agent?artifact=a%2Fb&key=x&key=y"
            assert body == request_body
            assert forwarded["Authorization"] == f"Bearer {'frame-old' if index == 0 else 'frame-new'}"
            assert forwarded["x-claude-session-id"] == "frame-session"
        assert len(refreshes) == (0 if auth_case == "accepted" else 1)
        if refreshes:
            assert refreshes[0]["grant_type"] == "refresh_token"
            assert refreshes[0]["refresh_token"] == "refresh-frame-account"
        async with SessionLocal() as session:
            logs = (await session.execute(select(RequestLog))).scalars().all()
            assert len(logs) == 1
            assert logs[0].request_kind == "frame"
            assert logs[0].caller_seat == "raise-desk-watch"
            assert logs[0].session_id == "frame-session"
            assert logs[0].account_id == "frame-account"
            assert logs[0].status == ("error" if auth_case == "rejected" else "success")
            assert logs[0].caller_user is not None
            assert logs[0].caller_machine is not None
            account = await session.get(Account, "frame-account")
            assert account.status == AccountStatus.ACTIVE
    finally:
        await runner.cleanup()
