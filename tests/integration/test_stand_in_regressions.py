from __future__ import annotations

import json

import pytest
from fastapi.responses import JSONResponse

from app.modules.proxy import api as proxy_api
from app.modules.proxy import stand_in
from app.modules.proxy.anthropic_service import AnthropicProxyError, AnthropicProxyService, AnthropicProxyStream

pytestmark = pytest.mark.integration


@pytest.fixture
def stand_in_turn(monkeypatch, tmp_path):
    table = tmp_path / "routing.json"
    table.write_text(
        json.dumps({"policy": {"stand_in": {"orchestrator": "sol-latest-high", "custom": "sol-latest-high"}}})
    )
    monkeypatch.setenv("ROUTE_TABLE", str(table))
    store = tmp_path / "stand-ins.json"
    monkeypatch.setenv("AGENT_LB_STAND_IN_FILE", str(store))
    mode = {"value": "ok", "bridge_status": 200}

    async def no_limits(*args, **kwargs):
        return None

    async def stream_messages(self, payload, headers, **kwargs):
        if mode["value"] == "fail":
            raise AnthropicProxyError(503, "empty pool", code="no_available_anthropic_accounts")

        async def body():
            if mode["value"] != "empty":
                yield b'{"id":"msg_claude"}'

        return AnthropicProxyStream(body=body(), media_type="application/json")

    async def bridge(*args, **kwargs):
        return JSONResponse({"answer": "stand-in"}, status_code=mode["bridge_status"])

    monkeypatch.setattr(proxy_api, "_enforce_request_limits", no_limits)
    monkeypatch.setattr(AnthropicProxyService, "stream_messages", stream_messages)
    monkeypatch.setattr(proxy_api, "_ccgpt_messages_response", bridge)
    return store, mode


async def send(client, *, intent="orchestrator", model="claude-opus-5-5", stream=True):
    headers = {"x-claude-session-id": "session"}
    if intent:
        headers["x-agent-lb-intent"] = intent
    return await client.post(
        "/v1/messages",
        headers=headers,
        json={"model": model, "max_tokens": 10, "stream": stream, "messages": [{"role": "user", "content": "next"}]},
    )


@pytest.mark.asyncio
async def test_untagged_nonstream_success_never_reads_the_store(async_client, stand_in_turn, monkeypatch):
    def unexpected_read(*args):
        pytest.fail("untagged request accessed the stand-in store")

    monkeypatch.setattr(stand_in, "_read", unexpected_read)
    response = await send(async_client, intent=None, stream=False)
    assert response.status_code == 200 and response.json()["id"] == "msg_claude"


@pytest.mark.asyncio
@pytest.mark.parametrize("contents", ["", "broken", "[]", "{}", '{"active":null,"recent":[]}'])
async def test_invalid_store_does_not_break_claude_or_stand_in(async_client, stand_in_turn, contents, caplog):
    store, mode = stand_in_turn
    store.write_text(contents)
    response = await send(async_client, stream=False)
    assert response.status_code == 200 and response.json()["id"] == "msg_claude"
    assert "Unable to read stand-in store" in caplog.text
    mode["value"] = "fail"
    response = await send(async_client)
    assert response.status_code == 200 and response.json()["answer"] == "stand-in"
    assert stand_in.snapshot()["active"][0]["requests"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["ok", "fail"])
async def test_store_write_errors_do_not_change_the_response(async_client, stand_in_turn, monkeypatch, value, caplog):
    _, mode = stand_in_turn
    mode["value"] = value

    def unavailable(*args, **kwargs):
        raise OSError("store unavailable")

    monkeypatch.setattr(stand_in, "record_failure", unavailable)
    monkeypatch.setattr(stand_in, "record_success", unavailable)
    response = await send(async_client, stream=False)
    assert response.status_code == 200
    assert response.json() == ({"id": "msg_claude"} if value == "ok" else {"answer": "stand-in"})
    assert "Unable to record stand-in" in caplog.text


@pytest.mark.asyncio
async def test_custom_policy_intent_stands_in(async_client, stand_in_turn):
    _, mode = stand_in_turn
    mode["value"] = "fail"
    response = await send(async_client, intent="custom")
    assert response.status_code == 200 and response.json()["answer"] == "stand-in"
    assert stand_in.snapshot()["active"][0]["intent"] == "custom"


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["claude-haiku-4-5", "claude-other-5"])
async def test_side_model_requests_neither_stand_in_nor_clear_records(async_client, stand_in_turn, model):
    _, mode = stand_in_turn
    mode["value"] = "fail"
    assert (await send(async_client)).status_code == 200
    response = await send(async_client, model=model)
    assert response.status_code == 503 and "x-agent-lb-standing-in" not in response.headers
    mode["value"] = "ok"
    assert (await send(async_client, model=model, stream=False)).status_code == 200
    assert len(stand_in.snapshot()["active"]) == 1


@pytest.mark.asyncio
async def test_empty_stream_does_not_mark_a_return(async_client, stand_in_turn):
    _, mode = stand_in_turn
    mode["value"] = "fail"
    assert (await send(async_client)).status_code == 200
    mode["value"] = "empty"
    response = await send(async_client)
    assert response.status_code == 200 and response.content == b""
    assert len(stand_in.snapshot()["active"]) == 1


@pytest.mark.asyncio
async def test_bridge_error_does_not_create_an_active_record(async_client, stand_in_turn):
    _, mode = stand_in_turn
    mode.update(value="fail", bridge_status=503)
    assert (await send(async_client)).status_code == 503
    assert stand_in.snapshot()["active"] == []


def test_store_read_oserror_returns_empty(stand_in_turn, monkeypatch, caplog):
    store, _ = stand_in_turn
    store.mkdir()
    assert stand_in.snapshot() == {"active": [], "recent": []}
    assert "Unable to read stand-in store" in caplog.text
