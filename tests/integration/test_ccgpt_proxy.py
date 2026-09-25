from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.responses import StreamingResponse

from app.core.clients.proxy import ProxyResponseError
from app.modules.proxy import api as proxy_api
from app.modules.proxy.claude_codex_bridge import CCGPT_MODEL, CCGPT_WORKER_MODEL

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_ccgpt_messages_uses_openai_responses_path_and_returns_anthropic_sse(
    async_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    async def fake_stream(request, payload, context, api_key, **kwargs):
        captured["payload"] = payload
        captured["kwargs"] = kwargs

        async def source():
            yield 'data: {"type":"response.created","response":{"id":"resp_live","model":"gpt-6-sol"}}\n\n'
            yield 'data: {"type":"response.output_text.delta","delta":"bridge ok"}\n\n'
            yield 'data: {"type":"response.completed","response":{"usage":{"input_tokens":7,"output_tokens":2}}}\n\n'

        return StreamingResponse(source(), media_type="text/event-stream")

    monkeypatch.setattr(proxy_api, "_stream_responses", fake_stream)

    async with async_client.stream(
        "POST",
        "/v1/ccgpt/messages",
        json={
            "model": "claude-opus-4-6",
            "max_tokens": 1024,
            "stream": True,
            "messages": [{"role": "user", "content": "hello"}],
        },
        headers={
            "authorization": "Bearer claude-secret",
            "anthropic-beta": "secret-beta",
            "X-Claude-Code-Session-Id": "coordinator-session",
        },
    ) as response:
        body = (await response.aread()).decode()

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert '"type":"message_start"' in body
    assert '"type":"text_delta","text":"bridge ok"' in body
    assert '"type":"message_stop"' in body
    translated = captured["payload"]
    assert translated.model == CCGPT_MODEL
    assert translated.reasoning.effort == "high"
    assert translated.service_tier == "priority"
    assert translated.to_payload()["service_tier"] == "priority"
    kwargs = captured["kwargs"]
    assert kwargs["forwarded_headers"].get("authorization") is None
    assert kwargs["forwarded_headers"].get("anthropic-beta") is None
    assert kwargs["forwarded_headers"]["x-claude-code-session-id"] == "coordinator-session"
    del kwargs["forwarded_headers"]
    assert kwargs == {
        "client_session_id": "coordinator-session",
        "codex_session_affinity": True,
        "openai_cache_affinity": True,
        "prefer_http_bridge": True,
        "locked_model": "gpt-6-sol",
        "locked_reasoning_effort": "high",
        "locked_service_tier": "priority",
    }


@pytest.mark.asyncio
async def test_ccgpt_messages_propagates_per_task_effort(async_client, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    async def fake_stream(request, payload, context, api_key, **kwargs):
        captured["payload"] = payload
        captured["kwargs"] = kwargs

        async def source():
            yield 'data: {"type":"response.completed","response":{"usage":{}}}\n\n'

        return StreamingResponse(source(), media_type="text/event-stream")

    monkeypatch.setattr(proxy_api, "_stream_responses", fake_stream)
    response = await async_client.post(
        "/v1/ccgpt/messages",
        json={
            "model": "gpt-6-sol",
            "max_tokens": 1024,
            "stream": True,
            "output_config": {"effort": "xhigh"},
            "messages": [{"role": "user", "content": "hello"}],
        },
    )

    assert response.status_code == 200
    assert captured["payload"].reasoning.effort == "xhigh"
    assert captured["kwargs"]["locked_reasoning_effort"] == "xhigh"
    assert captured["kwargs"]["locked_service_tier"] == "priority"
    assert captured["kwargs"]["client_session_id"] is None


@pytest.mark.asyncio
async def test_ccgpt_context_overflow_returns_prompt_too_long_not_api_error(
    async_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Upstream rejects an over-limit turn with context_length_exceeded before
    # streaming begins; _stream_responses turns that into the real non-streaming
    # error response the ccgpt handler must translate. Claude Code only reactive-
    # compacts when the error message contains "prompt is too long" — a bare
    # api_error drives the identical-retry storm this regression guards against.
    overflow_envelope = {
        "error": {
            "type": "invalid_request_error",
            "code": "context_length_exceeded",
            "param": "input",
            "message": "Your input exceeds the context window of this model.",
        }
    }

    async def fake_stream(request, payload, context, api_key, **kwargs):
        return proxy_api._stream_startup_error_response(
            request,
            ProxyResponseError(400, overflow_envelope),
            headers={},
        )

    monkeypatch.setattr(proxy_api, "_stream_responses", fake_stream)

    response = await async_client.post(
        "/v1/ccgpt/messages",
        json={
            "model": "claude-opus-4-6",
            "max_tokens": 1024,
            "stream": True,
            "messages": [{"role": "user", "content": "way too much context"}],
        },
    )

    assert response.status_code == 400
    body = response.json()
    assert body["type"] == "error"
    assert body["error"]["type"] == "invalid_request_error"
    assert "prompt is too long" in body["error"]["message"].lower()


@pytest.mark.asyncio
async def test_ccgpt_midstream_context_overflow_emits_prompt_too_long_without_success(
    async_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Upstream streams real assistant content, THEN fails mid-stream with a
    # context_length_exceeded envelope on a `response.failed` SSE event. Because
    # the assistant turn already has visible content, this is a genuine
    # mid-stream failure: it MUST stay an HTTP 200 SSE stream carrying an
    # Anthropic invalid_request_error `error` event whose message contains
    # "prompt is too long", and it MUST NOT close the turn with a normal empty
    # success (message_delta/message_stop) after the error.
    async def fake_stream(request, payload, context, api_key, **kwargs):
        async def source():
            yield 'data: {"type":"response.created","response":{"id":"resp_live","model":"gpt-6-sol"}}\n\n'
            yield 'data: {"type":"response.output_text.delta","delta":"partial answer"}\n\n'
            yield (
                'data: {"type":"response.failed","response":{"error":{'
                '"code":"context_length_exceeded",'
                '"message":"Your input exceeds the context window of this model."}}}\n\n'
            )

        return StreamingResponse(source(), media_type="text/event-stream")

    monkeypatch.setattr(proxy_api, "_stream_responses", fake_stream)

    async with async_client.stream(
        "POST",
        "/v1/ccgpt/messages",
        json={
            "model": "claude-opus-4-6",
            "max_tokens": 1024,
            "stream": True,
            "messages": [{"role": "user", "content": "way too much context"}],
        },
    ) as response:
        body = (await response.aread()).decode()

    assert response.status_code == 200
    assert '"type":"message_start"' in body
    assert "partial answer" in body
    assert '"type":"error"' in body
    assert '"type":"invalid_request_error"' in body
    assert "prompt is too long" in body.lower()
    assert "message_stop" not in body
    assert "message_delta" not in body


@pytest.mark.asyncio
async def test_ccgpt_precontent_stream_overflow_returns_http_400(async_client, monkeypatch: pytest.MonkeyPatch) -> None:
    # Upstream emits `response.created` fast (so the pre-stream HTTP probe sees a
    # non-error first item and commits to a streaming response), then a terminal
    # context overflow arrives BEFORE any assistant content. Claude Code only
    # reactive-compacts on a non-200 HTTP response received before it creates the
    # assistant turn; an `error` SSE frame after `message_start` under HTTP 200 is
    # dropped and the harness storms identical over-limit retries. The endpoint
    # MUST surface a pre-content terminal overflow as an HTTP 400 Anthropic
    # `invalid_request_error` with no `message_start` in the body.
    async def fake_stream(request, payload, context, api_key, **kwargs):
        async def source():
            yield 'data: {"type":"response.created","response":{"id":"resp_live","model":"gpt-6-sol"}}\n\n'
            yield (
                'data: {"type":"response.failed","response":{"error":{'
                '"code":"context_length_exceeded",'
                '"message":"Your input exceeds the context window of this model."}}}\n\n'
            )

        return StreamingResponse(source(), media_type="text/event-stream")

    monkeypatch.setattr(proxy_api, "_stream_responses", fake_stream)

    response = await async_client.post(
        "/v1/ccgpt/messages",
        json={
            "model": "claude-opus-4-6",
            "max_tokens": 1024,
            "stream": True,
            "messages": [{"role": "user", "content": "way too much context"}],
        },
    )

    assert response.status_code == 400
    body = response.json()
    assert body["type"] == "error"
    assert body["error"]["type"] == "invalid_request_error"
    assert "prompt is too long" in body["error"]["message"].lower()
    assert "message_start" not in json.dumps(body)


@pytest.mark.asyncio
async def test_ccgpt_precontent_top_level_overflow_frame_returns_http_400(
    async_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Same pre-content overflow, but delivered through the ChatGPT-backed Codex
    # top-level `error` frame shape (detail fields on the event root). It slips
    # past the pre-stream probe as a later frame and must still become HTTP 400.
    async def fake_stream(request, payload, context, api_key, **kwargs):
        async def source():
            yield 'data: {"type":"response.created","response":{"id":"resp_live","model":"gpt-6-sol"}}\n\n'
            yield (
                'data: {"type":"error","status_code":400,"error_type":"invalid_request_error",'
                '"code":"context_length_exceeded",'
                '"message":"Your input exceeds the context window of this model."}\n\n'
            )

        return StreamingResponse(source(), media_type="text/event-stream")

    monkeypatch.setattr(proxy_api, "_stream_responses", fake_stream)

    response = await async_client.post(
        "/v1/ccgpt/messages",
        json={
            "model": "claude-opus-4-6",
            "max_tokens": 1024,
            "stream": True,
            "messages": [{"role": "user", "content": "way too much context"}],
        },
    )

    assert response.status_code == 400
    body = response.json()
    assert body["error"]["type"] == "invalid_request_error"
    assert "prompt is too long" in body["error"]["message"].lower()


@pytest.mark.asyncio
async def test_ccgpt_non_overflow_error_stays_api_error(async_client, monkeypatch: pytest.MonkeyPatch) -> None:
    generic_envelope = {"error": {"type": "server_error", "message": "upstream exploded"}}

    async def fake_stream(request, payload, context, api_key, **kwargs):
        return proxy_api._stream_startup_error_response(
            request,
            ProxyResponseError(500, generic_envelope),
            headers={},
        )

    monkeypatch.setattr(proxy_api, "_stream_responses", fake_stream)

    response = await async_client.post(
        "/v1/ccgpt/messages",
        json={
            "model": "claude-opus-4-6",
            "max_tokens": 1024,
            "stream": True,
            "messages": [{"role": "user", "content": "hello"}],
        },
    )

    assert response.status_code == 500
    body = response.json()
    assert body["error"]["type"] == "api_error"
    assert body["error"]["message"] == "upstream exploded"
    assert "prompt is too long" not in json.dumps(body).lower()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403])
async def test_ccgpt_account_rejection_is_retryable_not_a_login_prompt(
    async_client, monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    # Every Codex account rejected the turn. The caller's own key is fine, so the
    # client must get a retryable error, not the 401/403 that makes Claude Code
    # stop and ask for /login.
    async def fake_stream(request, payload, context, api_key, **kwargs):
        return proxy_api._stream_startup_error_response(
            request,
            ProxyResponseError(status, {"error": {"code": "forbidden", "message": "Forbidden"}}),
            headers={},
        )

    monkeypatch.setattr(proxy_api, "_stream_responses", fake_stream)

    response = await async_client.post(
        "/v1/ccgpt/messages",
        json={
            "model": "claude-opus-4-6",
            "max_tokens": 1024,
            "stream": True,
            "messages": [{"role": "user", "content": "hello"}],
        },
    )

    assert response.status_code == 503
    assert response.json()["error"]["type"] == "api_error"


@pytest.mark.asyncio
async def test_ccgpt_stream_reports_usage_the_way_anthropic_does(async_client, monkeypatch: pytest.MonkeyPatch) -> None:
    # Claude Code can record a turn's content blocks before message_delta arrives,
    # with message_start's usage; a 0/0 start shows as "0 tok". The final usage
    # splits OpenAI's input (which includes cached tokens) into uncached input and
    # cache reads, as Anthropic reports them.
    async def fake_stream(request, payload, context, api_key, **kwargs):
        async def source():
            yield 'data: {"type":"response.created","response":{"id":"resp_usage","model":"gpt-6-sol"}}\n\n'
            yield 'data: {"type":"response.output_text.delta","delta":"ok"}\n\n'
            yield (
                'data: {"type":"response.completed","response":{"usage":{"input_tokens":8258,'
                '"output_tokens":363,"input_tokens_details":{"cached_tokens":7424}}}}\n\n'
            )

        return StreamingResponse(source(), media_type="text/event-stream")

    monkeypatch.setattr(proxy_api, "_stream_responses", fake_stream)

    async with async_client.stream(
        "POST",
        "/v1/ccgpt/messages",
        json={
            "model": "claude-opus-4-6",
            "max_tokens": 1024,
            "stream": True,
            "messages": [{"role": "user", "content": "hello " * 400}],
        },
    ) as response:
        body = (await response.aread()).decode()

    events = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]
    start = next(event for event in events if event["type"] == "message_start")
    delta = next(event for event in events if event["type"] == "message_delta")
    assert start["message"]["usage"]["input_tokens"] > 400
    assert delta["usage"] == {
        "input_tokens": 834,
        "cache_read_input_tokens": 7424,
        "cache_creation_input_tokens": 0,
        "output_tokens": 363,
    }


@pytest.mark.asyncio
async def test_ccgpt_count_tokens_is_local_and_native(async_client) -> None:
    response = await async_client.post(
        "/v1/ccgpt/messages/count_tokens",
        json={"model": "caller-model", "messages": [{"role": "user", "content": "hello"}]},
    )

    assert response.status_code == 200
    assert isinstance(response.json()["input_tokens"], int)
    assert response.json()["input_tokens"] > 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("alias", "expected_model", "expected_effort"),
    [
        ("gpt-6-sol-medium", CCGPT_MODEL, "medium"),
        ("gpt-6-sol-xhigh", CCGPT_MODEL, "xhigh"),
        ("gpt-6-luna-medium", CCGPT_WORKER_MODEL, "medium"),
    ],
)
async def test_messages_route_serves_worker_alias_via_bridge(
    async_client,
    monkeypatch: pytest.MonkeyPatch,
    alias: str,
    expected_model: str,
    expected_effort: str,
) -> None:
    captured: dict[str, object] = {}

    async def fake_stream(request, payload, context, api_key, **kwargs):
        captured["payload"] = payload
        captured["kwargs"] = kwargs

        async def source():
            yield f'data: {{"type":"response.created","response":{{"id":"resp_alias","model":"{expected_model}"}}}}\n\n'
            yield 'data: {"type":"response.output_text.delta","delta":"alias ok"}\n\n'
            yield 'data: {"type":"response.completed","response":{"usage":{"input_tokens":3,"output_tokens":1}}}\n\n'

        return StreamingResponse(source(), media_type="text/event-stream")

    monkeypatch.setattr(proxy_api, "_stream_responses", fake_stream)

    async with async_client.stream(
        "POST",
        "/v1/messages",
        json={
            "model": alias,
            "max_tokens": 512,
            "stream": True,
            "messages": [{"role": "user", "content": "hello"}],
        },
    ) as response:
        body = (await response.aread()).decode()

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert '"type":"message_start"' in body
    assert f'"model":"{expected_model}"' in body
    assert '"type":"text_delta","text":"alias ok"' in body
    assert captured["payload"].model == expected_model
    assert captured["kwargs"]["locked_model"] == expected_model
    assert captured["kwargs"]["locked_reasoning_effort"] == expected_effort
    assert captured["kwargs"]["locked_service_tier"] == "priority"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "model", "expected_model", "expected_effort"),
    [
        ("/v1/messages", "sol-latest", "gpt-7-sol", None),
        ("/v1/messages", "sol-latest-low", "gpt-7-sol", "low"),
        ("/v1/messages", "luna-latest-xhigh", CCGPT_WORKER_MODEL, "xhigh"),
        ("/v1/messages", "gpt-7-sol-high", "gpt-7-sol", "high"),
        ("/v1/ccgpt/messages", "gpt-6-luna-low", CCGPT_WORKER_MODEL, "low"),
    ],
)
async def test_gpt_model_names_resolve_from_the_served_model_list(
    async_client,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    model: str,
    expected_model: str,
    expected_effort: str | None,
) -> None:
    # gpt-7-sol stands in for a release the code has never heard of.
    served = dict.fromkeys(["gpt-5.6-sol", "gpt-6-sol", "gpt-7-sol", "gpt-6-luna", "codex-auto-review"])
    monkeypatch.setattr(
        proxy_api, "get_model_registry", lambda: SimpleNamespace(get_models_with_fallback=lambda: served)
    )
    captured: dict[str, object] = {}

    async def fake_stream(request, payload, context, api_key, **kwargs):
        captured["kwargs"] = kwargs

        async def source():
            yield 'data: {"type":"response.completed","response":{"usage":{}}}\n\n'

        return StreamingResponse(source(), media_type="text/event-stream")

    monkeypatch.setattr(proxy_api, "_stream_responses", fake_stream)
    response = await async_client.post(
        path,
        json={
            "model": model,
            "max_tokens": 512,
            "stream": True,
            "messages": [{"role": "user", "content": "hello"}],
        },
    )

    assert response.status_code == 200
    assert captured["kwargs"]["locked_model"] == expected_model
    assert captured["kwargs"]["locked_reasoning_effort"] == (expected_effort or "high")


@pytest.mark.asyncio
async def test_bridged_turns_of_one_conversation_share_a_stable_prefix_and_cache_key(
    async_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Two turns of one Claude Code conversation and a parallel subagent in the same
    # session, as sent through the first-party MITM: the billing block's cch changes
    # on every request and cache_control moves off the first message after turn 1.
    captured: list = []

    async def fake_stream(request, payload, context, api_key, **kwargs):
        captured.append(payload.model_copy(deep=True))

        async def source():
            yield 'data: {"type":"response.completed","response":{"usage":{}}}\n\n'

        return StreamingResponse(source(), media_type="text/event-stream")

    monkeypatch.setattr(proxy_api, "_stream_responses", fake_stream)
    session = json.dumps({"device_id": "d", "session_id": "8d5c04bd-aa63-46d7-be81-03b561927d46"})
    reminder = "<system-reminder>shared CLAUDE.md text</system-reminder>"

    def request(cch: str, messages: list) -> dict:
        return {
            "model": "gpt-6-sol-medium",
            "max_tokens": 512,
            "stream": True,
            "metadata": {"user_id": session},
            "system": [
                {"type": "text", "text": f"x-anthropic-billing-header: cc_version=2.1.282.b25; cch={cch};"},
                {"type": "text", "text": "You are a Claude agent."},
            ],
            "messages": messages,
        }

    def first(task: str, *, cached: bool) -> dict:
        block = {"type": "text", "text": f"{reminder}\n{task}"}
        if cached:
            block["cache_control"] = {"type": "ephemeral"}
        return {"role": "user", "content": [block]}

    turn_1 = request("a4129", [first("implement the parser", cached=True)])
    turn_2 = request(
        "7727b",
        [
            first("implement the parser", cached=False),
            {"role": "assistant", "content": [{"type": "text", "text": "on it"}]},
            {"role": "user", "content": [{"type": "text", "text": "continue", "cache_control": {"type": "ephemeral"}}]},
        ],
    )
    sibling = request("91c0e", [first("review the tests", cached=True)])
    for body in (turn_1, turn_2, sibling):
        assert (await async_client.post("/v1/messages", json=body)).status_code == 200

    one, two, other = captured
    assert "x-anthropic-billing-header" not in one.instructions
    assert one.instructions == two.instructions == "You are a Claude agent."
    assert one.prompt_cache_key is not None
    assert one.prompt_cache_key == two.prompt_cache_key
    assert other.prompt_cache_key not in (None, one.prompt_cache_key)


@pytest.mark.asyncio
async def test_ccgpt_route_refuses_an_unserved_gpt_name_instead_of_running_sol(
    async_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_stream(*args, **kwargs):
        raise AssertionError("an unserved model must not reach upstream")

    monkeypatch.setattr(proxy_api, "_stream_responses", fake_stream)
    response = await async_client.post(
        "/v1/ccgpt/messages",
        json={"model": "gpt-9-typo-low", "max_tokens": 512, "messages": [{"role": "user", "content": "hello"}]},
    )

    assert response.status_code == 400
    assert "gpt-9-typo-low" in response.json()["error"]["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize("alias", [CCGPT_MODEL, CCGPT_WORKER_MODEL])
async def test_messages_route_plain_alias_defers_to_request_effort(
    async_client, monkeypatch: pytest.MonkeyPatch, alias: str
) -> None:
    captured: dict[str, object] = {}

    async def fake_stream(request, payload, context, api_key, **kwargs):
        captured["kwargs"] = kwargs

        async def source():
            yield 'data: {"type":"response.completed","response":{"usage":{}}}\n\n'

        return StreamingResponse(source(), media_type="text/event-stream")

    monkeypatch.setattr(proxy_api, "_stream_responses", fake_stream)
    response = await async_client.post(
        "/v1/messages",
        json={
            "model": alias,
            "max_tokens": 512,
            "stream": True,
            "output_config": {"effort": "medium"},
            "messages": [{"role": "user", "content": "hello"}],
        },
    )

    assert response.status_code == 200
    assert captured["kwargs"]["locked_model"] == alias
    assert captured["kwargs"]["locked_reasoning_effort"] == "medium"


@pytest.mark.asyncio
@pytest.mark.parametrize("alias", ["gpt-6-sol-xhigh", "gpt-6-luna-xhigh"])
async def test_messages_count_tokens_worker_alias_is_local(async_client, alias: str) -> None:
    response = await async_client.post(
        "/v1/messages/count_tokens",
        json={"model": alias, "messages": [{"role": "user", "content": "hello"}]},
    )

    assert response.status_code == 200
    assert isinstance(response.json()["input_tokens"], int)
    assert response.json()["input_tokens"] > 0


@pytest.mark.asyncio
@pytest.mark.parametrize("thread", [{"type": "create"}, {"type": "continue", "previous_message_id": "msg_prev"}])
async def test_messages_route_refuses_message_threads_before_upstream(
    async_client, monkeypatch: pytest.MonkeyPatch, thread: dict[str, str]
) -> None:
    # Claude Code's message-threads beta sends continuation turns as a bare
    # delta (no system, no tools, no history). Forwarding one upstream hung the
    # session; the route must refuse it with the error code Claude Code reads
    # to resend the turn stateless.
    called = False

    async def fake_stream(request, payload, context, api_key, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("thread requests must not reach upstream")

    monkeypatch.setattr(proxy_api, "_stream_responses", fake_stream)

    response = await async_client.post(
        "/v1/messages",
        json={
            "model": "gpt-6-sol-low",
            "max_tokens": 512,
            "stream": True,
            "thread": thread,
            "messages": [
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": "hi"}]}
            ],
        },
    )

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["type"] == "invalid_request_error"
    assert error["details"] == {"error_code": "thread_unsupported_request"}
    assert called is False
