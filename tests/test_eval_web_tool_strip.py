from __future__ import annotations

import gzip
import json
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
@pytest.mark.parametrize(
    "provider,key_name,seat,web_types,normal,choice,expected_removed",
    [
        (
            "anthropic",
            "of-harbor",
            None,
            ["web_search_20250305", "web_fetch_20250910"],
            True,
            "web_search",
            ["web_search_20250305", "web_fetch_20250910"],
        ),
        ("anthropic", "ordinary", "eval.abc", ["web_fetch_20250910"], False, "web_fetch", ["web_fetch_20250910"]),
        ("anthropic", "ordinary", None, ["web_search_20250305"], True, "local", []),
        (
            "openai",
            "ordinary",
            " EVAL.abc ",
            ["web_search", "web_search_preview"],
            False,
            None,
            ["web_search", "web_search_preview"],
        ),
        ("openai", "of-harbor", None, ["web_search_preview"], True, None, ["web_search_preview"]),
        ("openai", "ordinary", "evaluation.abc", ["web_search"], True, None, []),
        ("openai", "ordinary", "eval.chat", ["web_search_preview"], False, "chat", ["web_search_preview"]),
        ("openai", "ordinary", "eval.chat", ["web_search_preview"], False, "chat-messages", ["web_search_preview"]),
    ],
)
async def test_eval_web_tools_at_upstream(
    async_client, monkeypatch, provider, key_name, seat, web_types, normal, choice, expected_removed
):
    received = []

    async def upstream(request):
        raw = await request.read()
        if raw.startswith(b"\x1f\x8b"):
            raw = gzip.decompress(raw)
        received.append(json.loads(raw))
        if provider == "anthropic":
            return web.json_response(
                {
                    "id": "msg_eval",
                    "type": "message",
                    "role": "assistant",
                    "model": "claude-sonnet-4-20250514",
                    "content": [{"type": "text", "text": "ok"}],
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 10, "output_tokens": 2},
                }
            )
        return web.Response(
            text="data: "
            + json.dumps(
                {
                    "type": "response.completed",
                    "response": {
                        "id": "resp_eval",
                        "object": "response",
                        "status": "completed",
                        "output": [],
                        "usage": {"input_tokens": 10, "output_tokens": 2},
                    },
                }
            )
            + "\n\n",
            content_type="text/event-stream",
        )

    server = web.Application()
    server.router.add_post("/v1/messages", upstream)
    server.router.add_post("/codex/responses", upstream)
    runner = web.AppRunner(server)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    base_url = f"http://127.0.0.1:{runner.addresses[0][1]}"
    monkeypatch.setenv("AGENT_LB_ANTHROPIC_UPSTREAM_BASE_URL", base_url)
    monkeypatch.setenv("AGENT_LB_UPSTREAM_BASE_URL", base_url)
    monkeypatch.setenv("AGENT_LB_UPSTREAM_STREAM_TRANSPORT", "http")
    monkeypatch.setenv("AGENT_LB_ANTHROPIC_CLAUDE_CODE_IDENTITY_ENABLED", "false")
    get_settings.cache_clear()
    try:
        encryptor = TokenEncryptor()
        async with SessionLocal() as session:
            session.add(
                Account(
                    id="eval-account",
                    provider=provider,
                    chatgpt_account_id="eval-account",
                    email="fixture@example.com",
                    plan_type="max" if provider == "anthropic" else "plus",
                    access_token_encrypted=encryptor.encrypt("fixture-token"),
                    refresh_token_encrypted=encryptor.encrypt("fixture-refresh"),
                    last_refresh=utcnow() + timedelta(days=1),
                    status=AccountStatus.ACTIVE,
                )
            )
            await session.commit()
        created = await async_client.post("/api/api-keys/", json={"name": key_name, "limits": []})
        assert created.status_code == 200, created.text
        headers = {"authorization": f"Bearer {created.json()['key']}"}
        if seat:
            headers["x-agent-lb-seat"] = seat
        tools = [
            {"type": kind, **({"name": kind.split("_202")[0]} if provider == "anthropic" else {})} for kind in web_types
        ]
        local = (
            {"name": "local", "input_schema": {"type": "object"}}
            if provider == "anthropic"
            else {
                "type": "function",
                "name": "local",
                "parameters": {"type": "object"},
            }
        )
        if normal:
            tools.append(local)
        if provider == "anthropic":
            payload = {
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 32,
                "messages": [{"role": "user", "content": "hello"}],
                "tools": tools,
                "tool_choice": {"type": "tool", "name": choice},
                "system": [{"type": "text", "text": "billing-marker"}, {"type": "text", "text": "instructions"}],
            }
            path = "/v1/messages"
        else:
            payload = {"model": "gpt-5.4", "instructions": "hello", "input": [], "stream": True, "tools": tools}
            path = "/backend-api/codex/responses"
        if choice in ("chat", "chat-messages"):
            path = "/v1/chat/completions"
            if choice == "chat-messages":
                payload.pop("input")
                payload["messages"] = [{"role": "user", "content": "hello"}]
        response = await async_client.post(path, json=payload, headers=headers)
        assert response.status_code == 200, response.text
        assert len(received) == 1, response.text
        forwarded = received[0]
        if expected_removed:
            if normal:
                assert forwarded["tools"] == [local]
            else:
                assert "tools" not in forwarded
            if provider == "anthropic":
                assert "tool_choice" not in forwarded
        else:
            assert forwarded["tools"] == tools
            if provider == "anthropic":
                assert forwarded["tool_choice"] == payload["tool_choice"]
        if provider == "anthropic":
            assert forwarded["system"][:2] == payload["system"]
        async with SessionLocal() as session:
            row = (await session.execute(select(RequestLog))).scalar_one()
        assert row.api_key_id == created.json()["id"]
        assert (json.loads(row.stripped_tools) if row.stripped_tools else []) == expected_removed
    finally:
        await runner.cleanup()
        get_settings.cache_clear()
