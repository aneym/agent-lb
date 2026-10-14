from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_backend_api_codex_v1_models_aliases_canonical(async_client) -> None:

    canonical = await async_client.get("/backend-api/codex/models")
    aliased = await async_client.get("/backend-api/codex/v1/models")

    assert canonical.status_code == aliased.status_code == 200
    assert canonical.json() == aliased.json()


@pytest.mark.asyncio
async def test_top_level_v1_models_is_unaffected(async_client) -> None:

    response = await async_client.get("/v1/models")
    assert response.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", ["/backend-api/codex", "/backend-api/codex/v1"])
async def test_codex_image_generation_uses_canonical_validation(async_client, prefix):
    payload = {"model": "not-an-image-model", "prompt": "route validation only"}
    canonical = await async_client.post("/v1/images/generations", json=payload)
    alias = await async_client.post(f"{prefix}/images/generations", json=payload)
    assert canonical.status_code == alias.status_code == 400
    assert canonical.json() == alias.json()


@pytest.mark.asyncio
async def test_codex_image_edit_uses_canonical_multipart_validation(async_client):
    form = {"model": "gpt-image-2", "prompt": "route validation only"}
    canonical = await async_client.post("/v1/images/edits", data=form)
    alias = await async_client.post("/backend-api/codex/images/edits", data=form)
    assert canonical.status_code == alias.status_code == 400
    assert canonical.json() == alias.json()
