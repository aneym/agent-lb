from __future__ import annotations

import json
from typing import Any, cast

import pytest

import app.modules.proxy.service as proxy_module
from tests.integration.proxy_images_support import (
    _disable_http_bridge as _disable_http_bridge,
)
from tests.integration.proxy_images_support import (
    _import_account as _import_account,
)
from tests.integration.proxy_images_support import (
    _sse as _sse,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_images_generations_unsupported_model_returns_400(async_client):
    response = await async_client.post(
        "/v1/images/generations",
        json={"model": "dall-e-3", "prompt": "a red circle"},
    )
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["type"] == "invalid_request_error"


@pytest.mark.asyncio
async def test_images_generations_rejects_transparent_background(async_client):
    response = await async_client.post(
        "/v1/images/generations",
        json={
            "model": "gpt-image-2",
            "prompt": "a red circle",
            "background": "transparent",
        },
    )
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["param"] == "background"


@pytest.mark.asyncio
async def test_images_generations_rejects_invalid_size_for_gpt_image_2(async_client):

    response = await async_client.post(
        "/v1/images/generations",
        json={
            "model": "gpt-image-2",
            "prompt": "a red circle",
            "size": "1023x1024",
        },
    )
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["param"] == "size"


@pytest.mark.asyncio
async def test_images_generations_legacy_size_rejected(async_client):
    response = await async_client.post(
        "/v1/images/generations",
        json={"model": "gpt-image-1", "prompt": "edit", "size": "2048x2048"},
    )
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["param"] == "size"


@pytest.mark.asyncio
async def test_images_generations_no_accounts_returns_5xx(async_client):
    response = await async_client.post(
        "/v1/images/generations",
        json={"model": "gpt-image-2", "prompt": "a red circle"},
    )
    assert response.status_code in (502, 503)
    body = response.json()
    assert body["error"]["type"] in {"server_error", "invalid_request_error"}


@pytest.mark.asyncio
async def test_images_generations_returns_envelope_on_success(async_client, monkeypatch):
    await _import_account(async_client, "acc_images_basic", "img-basic@example.com")

    captured: dict[str, object] = {}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **kwargs):
        del headers, access_token, base_url, raise_for_status, kwargs
        captured["model"] = payload.model
        captured["tools"] = list(payload.tools)
        captured["instructions"] = payload.instructions
        captured["input"] = payload.input
        captured["account_id"] = account_id
        yield _sse(
            {
                "type": "response.output_item.done",
                "output_index": 0,
                "item": {
                    "type": "image_generation_call",
                    "id": "ig_basic",
                    "status": "completed",
                    "result": "b64-image-bytes",
                    "revised_prompt": "a clean red circle",
                    "size": "1024x1024",
                    "quality": "low",
                    "background": "auto",
                    "output_format": "png",
                },
            }
        )
        yield _sse(
            {
                "type": "response.completed",
                "response": {
                    "id": "resp_images_basic",
                    "object": "response",
                    "status": "completed",
                    "tool_usage": {"image_gen": {"input_tokens": 7, "output_tokens": 13}},
                },
            }
        )

    async def fake_ensure_fresh(self, account, **kwargs):
        del self, kwargs
        return account

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh)

    response = await async_client.post(
        "/v1/images/generations",
        json={
            "model": "gpt-image-2",
            "prompt": "tiny red circle on white",
            "n": 1,
            "size": "1024x1024",
            "quality": "low",
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert "created" in body
    assert body["data"] == [{"b64_json": "b64-image-bytes", "revised_prompt": "a clean red circle"}]
    assert body["usage"] == {"input_tokens": 7, "output_tokens": 13, "total_tokens": 20}

    assert captured["model"] == "gpt-5.5"
    tools = cast(list[Any], captured["tools"])
    image_tool = cast(dict[str, Any], tools[0])
    assert image_tool["type"] == "image_generation"
    assert image_tool["model"] == "gpt-image-2"
    assert image_tool["size"] == "1024x1024"
    assert image_tool["quality"] == "low"


@pytest.mark.asyncio
async def test_images_generations_streaming_emits_canonical_events(async_client, monkeypatch):
    await _import_account(async_client, "acc_images_stream", "img-stream@example.com")

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **kwargs):
        del payload, headers, access_token, account_id, base_url, raise_for_status, kwargs
        yield _sse({"type": "response.created", "response": {"id": "resp_x"}})
        yield _sse({"type": "response.in_progress"})
        yield _sse({"type": "response.image_generation_call.in_progress"})
        yield _sse(
            {
                "type": "response.image_generation_call.partial_image",
                "partial_image_b64": "PART_0",
                "partial_image_index": 0,
                "size": "1024x1024",
                "quality": "low",
                "background": "auto",
                "output_format": "png",
                "output_index": 1,
            }
        )
        yield _sse(
            {
                "type": "response.output_item.done",
                "output_index": 1,
                "item": {
                    "type": "image_generation_call",
                    "id": "ig_stream",
                    "status": "completed",
                    "result": "FINAL_B64",
                    "revised_prompt": "neat",
                    "size": "1024x1024",
                    "quality": "low",
                    "background": "auto",
                    "output_format": "png",
                },
            }
        )
        yield _sse(
            {
                "type": "response.completed",
                "response": {
                    "id": "resp_x",
                    "tool_usage": {"image_gen": {"input_tokens": 1, "output_tokens": 2}},
                },
            }
        )

    async def fake_ensure_fresh(self, account, **kwargs):
        del self, kwargs
        return account

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh)

    async with async_client.stream(
        "POST",
        "/v1/images/generations",
        json={
            "model": "gpt-image-2",
            "prompt": "tiny red circle on white",
            "stream": True,
            "partial_images": 2,
            "size": "1024x1024",
            "quality": "low",
        },
    ) as resp:
        assert resp.status_code == 200
        body_lines = [line async for line in resp.aiter_lines()]

    payloads: list[dict[str, object]] = []
    last_done = False
    for line in body_lines:
        if not line:
            continue
        if line.strip() == "data: [DONE]":
            last_done = True
            continue
        if line.startswith("data: "):
            payloads.append(json.loads(line[len("data: ") :]))

    assert last_done is True
    types = [p["type"] for p in payloads]
    assert types == ["image_generation.partial_image", "image_generation.completed"]
    assert payloads[0]["b64_json"] == "PART_0"
    assert payloads[0]["partial_image_index"] == 0
    assert payloads[1]["b64_json"] == "FINAL_B64"
    assert payloads[1]["revised_prompt"] == "neat"


@pytest.mark.asyncio
async def test_images_generations_failed_image_returns_5xx(async_client, monkeypatch):
    await _import_account(async_client, "acc_images_failed", "img-failed@example.com")

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **kwargs):
        del payload, headers, access_token, account_id, base_url, raise_for_status, kwargs
        yield _sse(
            {
                "type": "response.output_item.done",
                "item": {
                    "type": "image_generation_call",
                    "status": "failed",
                    "error": {
                        "code": "content_policy_violation",
                        "message": "blocked",
                        "type": "invalid_request_error",
                    },
                },
            }
        )
        yield _sse({"type": "response.completed", "response": {}})

    async def fake_ensure_fresh(self, account, **kwargs):
        del self, kwargs
        return account

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh)

    response = await async_client.post(
        "/v1/images/generations",
        json={"model": "gpt-image-2", "prompt": "blocked"},
    )

    assert response.status_code in (400, 502)
    body = response.json()
    assert body["error"]["code"] in {"content_policy_violation", "image_generation_failed"}


@pytest.mark.asyncio
async def test_images_generations_falls_back_to_default_model_when_omitted(async_client, monkeypatch):

    await _import_account(async_client, "acc_images_default", "img-default@example.com")

    captured: dict[str, Any] = {}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **kwargs):
        del headers, access_token, account_id, base_url, raise_for_status, kwargs
        captured["tools"] = list(payload.tools)
        yield _sse(
            {
                "type": "response.output_item.done",
                "output_index": 0,
                "item": {
                    "type": "image_generation_call",
                    "id": "ig_default",
                    "status": "completed",
                    "result": "AAAA",
                },
            }
        )
        yield _sse({"type": "response.completed", "response": {"id": "resp_default"}})

    async def fake_ensure_fresh(self, account, **kwargs):
        del self, kwargs
        return account

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh)

    response = await async_client.post(
        "/v1/images/generations",
        json={"prompt": "no model given", "size": "1024x1024", "quality": "low"},
    )
    assert response.status_code == 200, response.text
    image_tool = cast(dict[str, Any], cast(list[Any], captured["tools"])[0])

    assert image_tool["model"] == "gpt-image-2"


@pytest.mark.asyncio
async def test_images_generations_rejects_n_greater_than_one(async_client):
    response = await async_client.post(
        "/v1/images/generations",
        json={"model": "gpt-image-2", "prompt": "x", "n": 2},
    )
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["param"] == "n"
    assert body["error"]["code"] == "invalid_request_error"


@pytest.mark.asyncio
async def test_images_generations_propagates_upstream_error_before_first_chunk(async_client, monkeypatch):

    from app.core.clients.proxy import ProxyResponseError

    await _import_account(async_client, "acc_images_prime", "img-prime@example.com")

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **kwargs):
        del payload, headers, access_token, account_id, base_url, raise_for_status, kwargs
        if False:
            yield ""
        raise ProxyResponseError(
            status_code=503,
            payload={"error": {"message": "boom", "type": "server_error", "code": "upstream_error"}},
        )

    async def fake_ensure_fresh(self, account, **kwargs):
        del self, kwargs
        return account

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh)

    response = await async_client.post(
        "/v1/images/generations",
        json={"model": "gpt-image-2", "prompt": "x", "size": "1024x1024", "quality": "low"},
    )
    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "upstream_error"


@pytest.mark.asyncio
async def test_images_generations_maps_content_policy_to_400(async_client, monkeypatch):

    await _import_account(async_client, "acc_images_cp", "img-cp@example.com")

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **kwargs):
        del payload, headers, access_token, account_id, base_url, raise_for_status, kwargs
        yield _sse(
            {
                "type": "response.output_item.done",
                "output_index": 0,
                "item": {
                    "type": "image_generation_call",
                    "id": "ig_cp",
                    "status": "failed",
                    "error": {
                        "code": "content_policy_violation",
                        "message": "policy violation",
                        "type": "invalid_request_error",
                    },
                },
            }
        )
        yield _sse({"type": "response.completed", "response": {"id": "resp_cp"}})

    async def fake_ensure_fresh(self, account, **kwargs):
        del self, kwargs
        return account

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh)

    response = await async_client.post(
        "/v1/images/generations",
        json={"model": "gpt-image-2", "prompt": "x", "size": "1024x1024", "quality": "low"},
    )
    assert response.status_code == 400, response.text
    body = response.json()
    assert body["error"]["code"] == "content_policy_violation"


@pytest.mark.asyncio
async def test_images_generations_rejects_input_fidelity(async_client):

    response = await async_client.post(
        "/v1/images/generations",
        json={
            "model": "gpt-image-1.5",
            "prompt": "x",
            "size": "1024x1024",
            "input_fidelity": "high",
        },
    )
    assert response.status_code == 400, response.text
    body = response.json()
    assert body["error"]["param"] == "input_fidelity"
    assert body["error"]["type"] == "invalid_request_error"


@pytest.mark.asyncio
async def test_images_generations_succeeds_when_reservation_finalize_fails(async_client, monkeypatch):

    await _import_account(async_client, "acc_images_finalize_fail", "img-fin-fail@example.com")

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **kwargs):
        del payload, headers, access_token, account_id, base_url, raise_for_status, kwargs
        yield _sse(
            {
                "type": "response.output_item.done",
                "output_index": 0,
                "item": {
                    "type": "image_generation_call",
                    "id": "ig_finfail",
                    "status": "completed",
                    "result": "B64_FINFAIL",
                },
            }
        )
        yield _sse(
            {
                "type": "response.completed",
                "response": {
                    "id": "resp_finfail",
                    "tool_usage": {"image_gen": {"input_tokens": 4, "output_tokens": 5}},
                },
            }
        )

    async def fake_ensure_fresh(self, account, **kwargs):
        del self, kwargs
        return account

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh)

    from app.modules.api_keys.service import ApiKeysService

    async def fake_finalize(self, *args, **kwargs):
        del self, args, kwargs
        raise RuntimeError("simulated DB failure during finalize")

    monkeypatch.setattr(ApiKeysService, "finalize_usage_reservation", fake_finalize)

    response = await async_client.post(
        "/v1/images/generations",
        json={
            "model": "gpt-image-2",
            "prompt": "x",
            "size": "1024x1024",
            "quality": "low",
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["data"] == [{"b64_json": "B64_FINFAIL"}]
