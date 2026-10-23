from __future__ import annotations

import pytest
from aiohttp import web

from app.db.session import SessionLocal
from app.modules.api_keys.repository import ApiKeysRepository
from tests.floor.test_model_source_routing import (
    _create_model_source,
    _enable_api_key_auth,
)
from tests.floor.test_model_source_routing import source_upstream as source_upstream

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["chat/completions", "responses", "embeddings"])
@pytest.mark.parametrize("pricing", ["missing", "audio_only", "free", "token"])
async def test_custom_source_cost_cap_requires_applicable_configured_price(
    async_client, source_upstream, endpoint: str, pricing: str
) -> None:
    await _enable_api_key_auth(async_client)
    forwarded: list[str] = []
    model = "custom-source-price"

    async def upstream(request: web.Request) -> web.Response:
        forwarded.append(request.path)
        return web.json_response(
            {
                "id": "source_price_response",
                "object": "response",
                "status": "completed",
                "model": model,
                "output": [],
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
                "data": [],
                "usage": {"prompt_tokens": 10, "input_tokens": 10, "completion_tokens": 0, "output_tokens": 0},
            }
        )

    source_id = await _create_model_source(
        async_client,
        name="cost-cap-source",
        model=model,
        base_url=await source_upstream(upstream),
        input_per_1m={"free": 0.0, "token": 2.0}.get(pricing),
        audio_per_minute=0.1 if pricing == "audio_only" else None,
        supports_responses=True,
        supports_embeddings=True,
    )
    created = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "source-cost-cap",
            "assignedSourceIds": [source_id],
            "limits": [{"limitType": "cost_usd", "limitWindow": "weekly", "maxValue": 1_000_000}],
        },
    )
    assert created.status_code == 200, created.text
    key = created.json()
    payload = {"model": model, "stream": False}
    if endpoint == "chat/completions":
        payload["messages"] = [{"role": "user", "content": "hi"}]
    else:
        payload["input"] = "hi"
    response = await async_client.post(
        f"/v1/{endpoint}",
        headers={"Authorization": f"Bearer {key['key']}", "x-request-has-source-pricing": "true"},
        json=payload,
    )
    if pricing in {"missing", "audio_only"}:
        assert response.status_code == 403, response.text
        assert response.json()["error"]["code"] == "model_unpriced_under_cost_cap"
        assert forwarded == []
        return
    assert response.status_code == 200, response.text
    assert forwarded == [f"/v1/{endpoint}"]
    async with SessionLocal() as session:
        limits = await ApiKeysRepository(session).get_limits_by_key(key["id"])
        assert limits[0].current_value == (20 if pricing == "token" else 0)
