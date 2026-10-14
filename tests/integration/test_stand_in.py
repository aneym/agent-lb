

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
from pathlib import Path

import pytest
from fastapi.responses import StreamingResponse

from app.modules.proxy import anthropic_service as anthropic_service_module
from app.modules.proxy import api as proxy_api
from app.modules.proxy.anthropic_service import AnthropicProxyError, AnthropicProxyStream

pytestmark = pytest.mark.integration
REPO = Path(__file__).resolve().parents[2]
RESET = 1_900_000_000


def turn(session: str, *, intent: str | None = "orchestrator", agent_id: str | None = None) -> tuple[dict, dict]:
    headers = {"X-Claude-Code-Session-Id": session, "x-agent-lb-lane": "routing"}
    if intent:
        headers["x-agent-lb-intent"] = intent
    if agent_id:
        headers["x-claude-code-agent-id"] = agent_id
    body = {"model": "claude-opus-5-5", "max_tokens": 1024, "stream": True,
            "messages": [{"role": "user", "content": "next step"}]}
    return body, headers


@pytest.mark.asyncio
async def test_an_orchestrator_stands_in_on_sol_while_claude_is_out_and_swaps_back_when_it_refills(
    async_client, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    table = tmp_path / "routing-table.json"
    table.write_text(json.dumps({"policy": {"stand_in": {"orchestrator": "sol-latest-high", "lane-tab": "sol-latest-high"}}}))
    monkeypatch.setenv("ROUTE_TABLE", str(table))
    monkeypatch.setenv("AGENT_LB_STAND_IN_FILE", str(tmp_path / "stand-ins.json"))
    anthropic = {"mode": "empty"}
    codex: list[dict] = []

    async def no_limits(*args: object, **kwargs: object) -> None:
        return None

    async def fake_stream_messages(self, payload, inbound_headers, *, api_key=None, api_key_reservation=None):
        if anthropic["mode"] == "empty":

            raise AnthropicProxyError(503, "No available Anthropic accounts", code="no_available_anthropic_accounts",
                                      retry_at=RESET)

        async def body():
            if anthropic["mode"] == "all-429":

                raise AnthropicProxyError(429, "rate limited on every account", code="rate_limit_error", retry_at=RESET)
            yield b'event: message_start\ndata: {"type":"message_start","message":{"id":"msg_claude","model":"claude-opus-5-5"}}\n\n'
            yield b'event: message_stop\ndata: {"type":"message_stop"}\n\n'

        return AnthropicProxyStream(body=body(), media_type="text/event-stream")

    async def fake_stream_responses(request, payload, context, api_key, **kwargs):
        codex.append({"locked_model": kwargs.get("locked_model"), "effort": kwargs.get("locked_reasoning_effort"),
                      "session": kwargs.get("client_session_id")})

        async def source():
            yield 'data: {"type":"response.created","response":{"id":"resp_sol","model":"gpt-6.1-sol"}}\n\n'
            yield 'data: {"type":"response.output_text.delta","delta":"sol stands in"}\n\n'
            yield 'data: {"type":"response.completed","response":{"usage":{"input_tokens":7,"output_tokens":3}}}\n\n'

        return StreamingResponse(source(), media_type="text/event-stream")

    monkeypatch.setattr(proxy_api, "_enforce_request_limits", no_limits)
    monkeypatch.setattr(anthropic_service_module.AnthropicProxyService, "stream_messages", fake_stream_messages)
    monkeypatch.setattr(proxy_api, "_stream_responses", fake_stream_responses)

    async def send(session: str, **kwargs) -> tuple[int, dict, str]:
        body, headers = turn(session, **kwargs)
        async with async_client.stream("POST", "/v1/messages", json=body, headers=headers) as response:
            return response.status_code, dict(response.headers), (await response.aread()).decode()


    status, headers, text = await send("orch-1")
    assert status == 200 and "sol stands in" in text
    assert codex[-1] == {"locked_model": "gpt-6.1-sol", "effort": "high", "session": "orch-1"}
    assert headers["x-agent-lb-standing-in"] == "gpt-6.1-sol for claude-opus-5-5"


    row = (await async_client.get("/api/pools/stand-ins")).json()["active"][0]
    assert {k: row[k] for k in ("session_id", "lane", "intent", "intended", "running", "effort", "reason", "requests",
                                "expected_return")} == {
        "session_id": "orch-1", "lane": "routing", "intent": "orchestrator", "intended": "claude-opus-5-5",
        "running": "gpt-6.1-sol", "effort": "high", "reason": "no_available_anthropic_accounts: No available Anthropic accounts",
        "requests": 1, "expected_return": "2030-03-17T17:46:40Z"}
    since = row["since"]
    assert "orch-1" in (tmp_path / "stand-ins.json").read_text()


    assert (await send("orch-1", agent_id="agent-7"))[0] == 429
    assert (await send("plain-1", intent=None))[0] == 429
    assert len(codex) == 1


    anthropic["mode"] = "all-429"
    status, headers, text = await send("orch-1")
    assert status == 200 and "sol stands in" in text
    row = (await async_client.get("/api/pools/stand-ins")).json()["active"][0]
    assert (row["requests"], row["since"], row["reason"]) == (2, since, "rate_limit_error: rate limited on every account")


    anthropic["mode"] = "ok"
    status, headers, text = await send("orch-1")
    assert status == 200 and "msg_claude" in text and "x-agent-lb-standing-in" not in headers
    assert len(codex) == 2
    state = (await async_client.get("/api/pools/stand-ins")).json()
    assert state["active"] == []
    assert state["recent"][0]["session_id"] == "orch-1" and state["recent"][0]["returned_at"]


def test_the_session_shim_tags_requests_with_intent_and_lane() -> None:
    loader = importlib.machinery.SourceFileLoader("claude_lb_launch_tags", str(REPO / "clients" / "claude-lb-launch"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    assert module.tag_headers({"AGENT_LB_INTENT": "lane-tab", "AGENT_LB_LANE": "routing", "HOME": "/x"}) == {
        "x-agent-lb-intent": "lane-tab", "x-agent-lb-lane": "routing"}
    assert module.tag_headers({"AGENT_LB_INTENT": ""}) == {}
