from __future__ import annotations

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
async def test_images_edits_basic_round_trip(async_client, monkeypatch):
    await _import_account(async_client, "acc_images_edit", "img-edit@example.com")

    captured: dict[str, object] = {}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **kwargs):
        del headers, access_token, base_url, raise_for_status, kwargs
        captured["input"] = payload.input
        captured["tools"] = list(payload.tools)
        captured["account_id"] = account_id
        yield _sse(
            {
                "type": "response.output_item.done",
                "item": {
                    "type": "image_generation_call",
                    "id": "ig_edit",
                    "status": "completed",
                    "result": "EDITED_B64",
                    "revised_prompt": "edited",
                    "size": "1024x1024",
                    "quality": "low",
                    "background": "auto",
                    "output_format": "png",
                },
            }
        )
        yield _sse({"type": "response.completed", "response": {}})

    async def fake_ensure_fresh(self, account, **kwargs):
        del self, kwargs
        return account

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh)

    image_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    response = await async_client.post(
        "/v1/images/edits",
        data={
            "model": "gpt-image-1",
            "prompt": "make it green",
            "size": "1024x1024",
            "quality": "low",
        },
        files={
            "image": ("source.png", image_bytes, "image/png"),
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["data"] == [{"b64_json": "EDITED_B64", "revised_prompt": "edited"}]

    input_value = cast(list[Any], captured["input"])
    assert input_value
    first_message = cast(dict[str, Any], input_value[0])
    content = cast(list[Any], first_message["content"])
    text_part = cast(dict[str, Any], content[0])
    assert text_part["type"] == "input_text"
    assert text_part["text"] == "make it green"
    image_parts: list[dict[str, Any]] = [
        cast(dict[str, Any], p) for p in content if isinstance(p, dict) and p.get("type") == "input_image"
    ]
    assert len(image_parts) == 1
    image_url_value = cast(str, image_parts[0]["image_url"])
    assert image_url_value.startswith("data:image/png;base64,")


@pytest.mark.asyncio
async def test_images_edits_input_fidelity_rejected_on_gpt_image_2(async_client):
    image_bytes = b"\x89PNG\r\n\x1a\n"
    response = await async_client.post(
        "/v1/images/edits",
        data={
            "model": "gpt-image-2",
            "prompt": "hi",
            "input_fidelity": "high",
        },
        files={"image": ("source.png", image_bytes, "image/png")},
    )
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["param"] == "input_fidelity"


@pytest.mark.asyncio
async def test_images_edits_unsupported_model(async_client):
    image_bytes = b"\x89PNG\r\n\x1a\n"
    response = await async_client.post(
        "/v1/images/edits",
        data={
            "model": "dall-e-3",
            "prompt": "hi",
        },
        files={"image": ("source.png", image_bytes, "image/png")},
    )
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["type"] == "invalid_request_error"


@pytest.mark.asyncio
async def test_images_variations_returns_404(async_client):
    response = await async_client.post(
        "/v1/images/variations",
        json={"model": "gpt-image-2", "prompt": "no"},
    )
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "not_found_error"


@pytest.mark.asyncio
async def test_images_edits_falls_back_to_default_model_when_omitted(async_client, monkeypatch):

    await _import_account(async_client, "acc_images_edit_default", "img-edit-default@example.com")

    captured: dict[str, Any] = {}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **kwargs):
        del headers, access_token, account_id, base_url, raise_for_status, kwargs
        captured["tools"] = list(payload.tools)
        yield _sse(
            {
                "type": "response.output_item.done",
                "item": {
                    "type": "image_generation_call",
                    "id": "ig_edit_default",
                    "status": "completed",
                    "result": "DEFAULT_EDITED_B64",
                },
            }
        )
        yield _sse({"type": "response.completed", "response": {"id": "resp_edit_default"}})

    async def fake_ensure_fresh(self, account, **kwargs):
        del self, kwargs
        return account

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh)

    image_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    response = await async_client.post(
        "/v1/images/edits",
        data={
            "prompt": "no model on edits",
            "size": "1024x1024",
            "quality": "low",
        },
        files={"image": ("source.png", image_bytes, "image/png")},
    )
    assert response.status_code == 200, response.text
    image_tool = cast(dict[str, Any], cast(list[Any], captured["tools"])[0])
    assert image_tool["model"] == "gpt-image-2"


@pytest.mark.asyncio
async def test_images_edits_invalid_n_returns_openai_error(async_client):

    image_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    response = await async_client.post(
        "/v1/images/edits",
        data={
            "model": "gpt-image-2",
            "prompt": "x",
            "n": "abc",
        },
        files={"image": ("source.png", image_bytes, "image/png")},
    )
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["type"] == "invalid_request_error"


@pytest.mark.asyncio
async def test_images_edits_invalid_stream_returns_openai_error(async_client):
    image_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    response = await async_client.post(
        "/v1/images/edits",
        data={
            "model": "gpt-image-2",
            "prompt": "x",
            "stream": "yesplz",
        },
        files={"image": ("source.png", image_bytes, "image/png")},
    )
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["type"] == "invalid_request_error"


@pytest.mark.asyncio
async def test_images_edits_accepts_image_brackets_form_key(async_client, monkeypatch):

    await _import_account(async_client, "acc_images_brackets", "img-brackets@example.com")

    captured: dict[str, Any] = {}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **kwargs):
        del headers, access_token, account_id, base_url, raise_for_status, kwargs
        captured["input"] = payload.input
        yield _sse(
            {
                "type": "response.output_item.done",
                "output_index": 0,
                "item": {
                    "type": "image_generation_call",
                    "id": "ig_brackets",
                    "status": "completed",
                    "result": "B64_BRACKETS",
                },
            }
        )
        yield _sse({"type": "response.completed", "response": {"id": "resp_brackets"}})

    async def fake_ensure_fresh(self, account, **kwargs):
        del self, kwargs
        return account

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh)

    image_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    response = await async_client.post(
        "/v1/images/edits",
        data={
            "model": "gpt-image-2",
            "prompt": "image[] form key test",
        },
        files={
            "image[]": ("source.png", image_bytes, "image/png"),
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["data"] == [{"b64_json": "B64_BRACKETS"}]

    input_value = cast(list[Any], captured["input"])
    first_message = cast(dict[str, Any], input_value[0])
    content = cast(list[Any], first_message["content"])
    image_parts = [cast(dict[str, Any], p) for p in content if isinstance(p, dict) and p.get("type") == "input_image"]
    assert len(image_parts) == 1
