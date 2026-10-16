from __future__ import annotations

from datetime import timedelta

import pytest
from aiohttp import web
from sqlalchemy import select

from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, RequestLog
from app.db.session import SessionLocal

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_anthropic_request_log_attribution(async_client, monkeypatch):
    received = []

    async def messages(request):
        received.append(await request.json())
        return web.json_response(
            {
                "id": "msg_attribution",
                "type": "message",
                "role": "assistant",
                "model": "claude-sonnet-4-20250514",
                "content": [{"type": "text", "text": "ok"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 10, "output_tokens": 2},
            }
        )

    upstream = web.Application()
    upstream.router.add_post("/v1/messages", messages)
    runner = web.AppRunner(upstream)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]
    monkeypatch.setenv("AGENT_LB_ANTHROPIC_UPSTREAM_BASE_URL", f"http://127.0.0.1:{port}")
    get_settings.cache_clear()
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        session.add(
            Account(
                id="attribution-account",
                provider="anthropic",
                chatgpt_account_id="attribution-account",
                email="fixture@example.com",
                plan_type="max",
                access_token_encrypted=encryptor.encrypt("fixture-token"),
                refresh_token_encrypted=encryptor.encrypt("fixture-refresh"),
                last_refresh=utcnow() + timedelta(days=1),
                status=AccountStatus.ACTIVE,
            )
        )
        await session.commit()
    cases = [
        ({"output_config": {"effort": "medium"}}, {"x-agent-lb-seat": "opus-seat"}, "medium", "opus-seat"),
        (
            {"thinking": {"type": "enabled", "budget_tokens": 20000}},
            {"x-agent-lb-dispatch": "dcn_01TEST"},
            "high",
            "dcn:dcn_01TEST",
        ),
        (
            {"output_config": {"effort": "max"}, "thinking": {"type": "enabled", "budget_tokens": 1000}},
            {"x-agent-lb-seat": "opus-seat", "x-agent-lb-dispatch": "dcn_BOTH"},
            "max",
            "opus-seat",
        ),
        ({}, {}, "none", None),
        ({"thinking": {"type": "disabled", "budget_tokens": 20000}}, {}, "none", None),
    ]
    cases.extend(
        ({"thinking": {"type": "enabled", "budget_tokens": budget}}, {}, effort, None)
        for budget, effort in [
            (3999, "low"),
            (4000, "medium"),
            (15999, "medium"),
            (16000, "high"),
            (31999, "high"),
            (32000, "xhigh"),
        ]
    )
    try:
        for index, (extra, headers, effort, seat) in enumerate(cases):
            response = await async_client.post(
                "/v1/messages",
                json={
                    "model": "claude-sonnet-4-20250514",
                    "max_tokens": 40000,
                    "messages": [{"role": "user", "content": "hello"}],
                    **extra,
                },
                headers=headers,
            )
            assert response.status_code == 200, response.text
            async with SessionLocal() as session:
                rows = (await session.execute(select(RequestLog).order_by(RequestLog.id))).scalars().all()
            assert len(rows) == index + 1
            row = rows[-1]
            assert row.reasoning_effort == effort
            assert row.caller_seat == seat
            dispatch = headers.get("x-agent-lb-dispatch")
            assert (row.unified_headers or {}).get("x-agent-lb-dispatch") == dispatch
        assert len(received) == len(cases)
    finally:
        await runner.cleanup()
        get_settings.cache_clear()
