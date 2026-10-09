"""HTTP/SQLite regression: a vendor probe restores routing after transient failures.

The vendor edge is an actual local HTTP server; no application collaborators are mocked.
"""

from datetime import datetime, timezone

import pytest
from aiohttp import web

from app.core.config.settings import get_settings
from tests.integration.test_anthropic_proxy import ANTHROPIC_JSON_BYTES, _insert_account

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
@pytest.mark.parametrize("probe_status,quota_limited", [(200, False), (503, False), (200, True)])
async def test_probe_reconciles_transient_selector_backoff(async_client, monkeypatch, probe_status, quota_limited):
    status = 429 if quota_limited else 403
    calls = 0

    async def messages(request):
        nonlocal calls
        calls += 1
        if status == 200:
            return web.Response(body=ANTHROPIC_JSON_BYTES, content_type="application/json")
        return web.json_response({"error": {"message": "temporary credential rejection"}}, status=status)

    async def usage(request):
        return web.json_response({"five_hour": {"utilization": 6}, "seven_day": {"utilization": 22}})

    vendor = web.Application()
    vendor.router.add_post("/v1/messages", messages)
    vendor.router.add_get("/api/oauth/usage", usage)
    runner = web.AppRunner(vendor)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    monkeypatch.setattr(get_settings(), "anthropic_upstream_base_url", f"http://127.0.0.1:{port}")
    try:
        await _insert_account(
            account_id="recover", provider="anthropic", access_token="test-token", email="recover@example.com"
        )
        payload = {"model": "claude-opus-5-5", "max_tokens": 8, "messages": [{"role": "user", "content": "hi"}]}
        for _ in range(1 if quota_limited else 3):
            assert (await async_client.post("/v1/messages", json=payload)).status_code in {403, 429, 503}
        assert calls == (1 if quota_limited else 3)
        route = {"sessionId": "recovery-session", "model": "claude-opus-5-5"}
        blocked = await async_client.post("/api/anthropic/session-route", json=route)
        assert blocked.status_code == 503
        error = blocked.json()["error"]
        if not quota_limited:
            assert "transient backoff excluded 1 account" in error["message"]
        retry = datetime.fromisoformat(error["retryAt"].replace("Z", "+00:00"))
        assert 0 < (retry - datetime.now(timezone.utc)).total_seconds() <= (60 if quota_limited else 30)
        status = probe_status
        probe = await async_client.post("/api/accounts/recover/probe", json={"model": "claude-opus-5-5"})
        assert probe.status_code == 200
        assert probe.json()["probeStatusCode"] == probe_status
        after = await async_client.post("/api/anthropic/session-route", json=route)
        expected = 200 if probe_status == 200 and not quota_limited else 503
        assert after.status_code == expected
        routed = await async_client.post("/v1/messages", json=payload)
        if expected == 200:
            assert routed.status_code == 200
        else:
            assert routed.status_code in {429, 503}
        if expected == 200:
            assert routed.json()["content"][0]["text"] == "ok"
    finally:
        await runner.cleanup()
