from __future__ import annotations

import pytest

from app.core.openai.model_registry import get_model_registry
from tests.integration.model_catalog_helpers import BOOTSTRAP_MODEL_SLUGS as BOOTSTRAP_MODEL_SLUGS
from tests.integration.model_catalog_helpers import _make_upstream_model as _make_upstream_model
from tests.integration.model_catalog_helpers import _populate_test_registry as _populate_test_registry
from tests.integration.model_catalog_helpers import _raw_with_max_context_window as _raw_with_max_context_window

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_v1_models_list(async_client):
    await _populate_test_registry()
    resp = await async_client.get("/v1/models")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["object"] == "list"
    data = payload["data"]
    assert isinstance(data, list)
    ids = {item["id"] for item in data}
    assert "gpt-5.2" in ids
    assert "gpt-5.3-codex" in ids
    for item in data:
        assert item["object"] == "model"
        assert item["owned_by"] == "agent-lb"
        assert "metadata" in item
        assert item["api_types"] == ["chat_completions"]
        assert item["capabilities"]["context_length"] == item["metadata"]["input_context_window"]
        assert item["capabilities"]["supports_tool_use"] is True
        assert item["capabilities"]["supports_streaming"] is True
        assert item["capabilities"]["output_modalities"] == ["text"]
        assert item["contextLength"] == item["metadata"]["input_context_window"]
        assert item["context_length"] == item["metadata"]["input_context_window"]
        assert item["supportsReasoning"] is True
        assert item["supports_reasoning"] is True
        assert item["supportsImages"] is True
        assert item["supports_images"] is True
        assert item["supportsVision"] is True
        assert item["supports_vision"] is True


@pytest.mark.asyncio
async def test_v1_models_also_carries_codex_models_field(async_client):

    await _populate_test_registry()
    resp = await async_client.get("/v1/models")
    assert resp.status_code == 200
    payload = resp.json()

    assert payload["object"] == "list"
    assert {item["id"] for item in payload["data"]} == {"gpt-5.2", "gpt-5.3-codex"}

    assert "models" in payload
    entries = payload["models"]
    assert {entry["slug"] for entry in entries} == {"gpt-5.2", "gpt-5.3-codex"}
    for entry in entries:
        assert entry["display_name"]
        assert "supported_reasoning_levels" in entry
        assert "context_window" in entry
        assert "id" not in entry


@pytest.mark.asyncio
async def test_v1_models_codex_field_matches_codex_route(async_client):
    await _populate_test_registry()
    v1 = await async_client.get("/v1/models")
    codex = await async_client.get("/backend-api/codex/models")
    assert v1.status_code == 200
    assert codex.status_code == 200
    assert v1.json()["models"] == codex.json()["models"]


@pytest.mark.asyncio
async def test_v1_models_uses_bootstrap_models_when_registry_not_populated(async_client):
    registry = get_model_registry()
    registry._snapshot = None
    resp = await async_client.get("/v1/models")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["object"] == "list"
    ids = {item["id"] for item in payload["data"]}
    assert ids == BOOTSTRAP_MODEL_SLUGS
    assert "gpt-5.5-pro" not in ids


@pytest.mark.asyncio
async def test_v1_models_includes_supported_in_api_false_models(async_client):
    registry = get_model_registry()
    models = [
        _make_upstream_model("gpt-5.2"),
        _make_upstream_model("gpt-5.3-codex"),
        _make_upstream_model("gpt-hidden", supported_in_api=False),
    ]
    await registry.update({"plus": models, "pro": models})

    resp = await async_client.get("/v1/models")
    assert resp.status_code == 200
    ids = {item["id"] for item in resp.json()["data"]}
    assert {"gpt-5.2", "gpt-5.3-codex", "gpt-hidden"}.issubset(ids)


@pytest.mark.asyncio
async def test_model_catalogs_canonicalize_enforced_model_alias(async_client):
    registry = get_model_registry()
    models = [
        _make_upstream_model(
            "gpt-5.2",
            raw={
                "shell_type": "shell_command",
                "visibility": "list",
            },
        ),
        _make_upstream_model(
            "gpt-5.4-mini",
            raw={
                "shell_type": "shell_command",
                "visibility": "hide",
            },
        ),
    ]
    await registry.update({"plus": models, "pro": models})

    enable = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "totpRequiredOnLogin": False,
            "apiKeyAuthEnabled": True,
        },
    )
    assert enable.status_code == 200

    created = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "catalog-enforced-alias",
            "allowedModels": ["gpt-5.4-mini-high"],
            "applyToCodexModel": True,
            "enforcedModel": "gpt-5.4-mini-high",
        },
    )
    assert created.status_code == 200
    key = created.json()["key"]

    v1_resp = await async_client.get("/v1/models", headers={"Authorization": f"Bearer {key}"})
    assert v1_resp.status_code == 200
    assert [entry["id"] for entry in v1_resp.json()["data"]] == ["gpt-5.4-mini"]

    codex_resp = await async_client.get("/backend-api/codex/models", headers={"Authorization": f"Bearer {key}"})
    assert codex_resp.status_code == 200
    entries = {entry["slug"]: entry for entry in codex_resp.json()["models"]}
    assert set(entries) == {"gpt-5.2", "gpt-5.4-mini"}
    assert entries["gpt-5.2"]["visibility"] == "hide"
    assert entries["gpt-5.4-mini"]["visibility"] == "list"


@pytest.mark.asyncio
async def test_model_sets_are_consistent_across_api_endpoints(async_client):
    registry = get_model_registry()
    models = [
        _make_upstream_model("gpt-5.2"),
        _make_upstream_model("gpt-5.3-codex"),
        _make_upstream_model("gpt-hidden", supported_in_api=False),
    ]
    await registry.update({"plus": models, "pro": models})

    dashboard = await async_client.get("/api/models")
    v1 = await async_client.get("/v1/models")
    codex = await async_client.get("/backend-api/codex/models")

    assert dashboard.status_code == 200
    assert v1.status_code == 200
    assert codex.status_code == 200

    dashboard_ids = {item["id"] for item in dashboard.json()["models"]}
    v1_ids = {item["id"] for item in v1.json()["data"]}
    codex_slugs = {item["slug"] for item in codex.json()["models"]}
    assert dashboard_ids == v1_ids == codex_slugs


@pytest.mark.asyncio
async def test_model_context_window_override(async_client, monkeypatch):
    registry = get_model_registry()
    models = [_make_upstream_model("gpt-5.4")]
    await registry.update({"pro": models})

    from app.core.config.settings import get_settings
    from app.modules.proxy import api as proxy_api_module

    original_settings = get_settings()
    patched = original_settings.model_copy(update={"model_context_window_overrides": {"gpt-5.4": 515000}})
    monkeypatch.setattr(proxy_api_module, "get_settings", lambda: patched)

    resp = await async_client.get("/backend-api/codex/models")
    assert resp.status_code == 200
    entry = next(m for m in resp.json()["models"] if m["slug"] == "gpt-5.4")
    assert entry["context_window"] == 515000

    resp_v1 = await async_client.get("/v1/models")
    assert resp_v1.status_code == 200
    v1_entry = next(m for m in resp_v1.json()["data"] if m["id"] == "gpt-5.4")
    metadata = v1_entry["metadata"]
    assert metadata["context_window"] == 515000
    assert metadata["input_context_window"] == 272000
    assert v1_entry["capabilities"]["context_length"] == 272000
    assert v1_entry["contextLength"] == 272000
    assert v1_entry["context_length"] == 272000


@pytest.mark.asyncio
async def test_model_context_window_no_override(async_client):
    registry = get_model_registry()
    models = [_make_upstream_model("gpt-5.4")]
    await registry.update({"pro": models})

    resp = await async_client.get("/backend-api/codex/models")
    assert resp.status_code == 200
    entry = next(m for m in resp.json()["models"] if m["slug"] == "gpt-5.4")
    assert entry["context_window"] == 272000


@pytest.mark.asyncio
async def test_v1_models_reports_backend_context_window(async_client):
    registry = get_model_registry()
    models = [
        _make_upstream_model("gpt-5.4", raw=_raw_with_max_context_window(1_000_000)),
        _make_upstream_model("gpt-5.5", raw=_raw_with_max_context_window(272_000)),
        _make_upstream_model("gpt-5.4-mini", raw=_raw_with_max_context_window(272_000)),
        _make_upstream_model("gpt-5.3-codex", raw=_raw_with_max_context_window(272_000)),
    ]
    await registry.update({"pro": models})

    resp_v1 = await async_client.get("/v1/models")
    assert resp_v1.status_code == 200
    metadata_by_id = {item["id"]: item["metadata"] for item in resp_v1.json()["data"]}

    for slug in ("gpt-5.4", "gpt-5.5", "gpt-5.4-mini", "gpt-5.3-codex"):
        metadata = metadata_by_id[slug]
        assert metadata["context_window"] == 272_000
        assert metadata["input_context_window"] == 272_000
        assert metadata["max_output_tokens"] == 128_000
        entry = next(item for item in resp_v1.json()["data"] if item["id"] == slug)
        assert entry["api_types"] == ["chat_completions"]
        assert entry["capabilities"]["context_length"] == 272_000
        assert entry["capabilities"]["max_output_tokens"] == 128_000
        assert entry["capabilities"]["supports_reasoning"] is True
        assert entry["capabilities"]["supportsImages"] is True
        assert entry["capabilities"]["supports_images"] is True
        assert entry["capabilities"]["supports_vision"] is True
        assert entry["capabilities"]["supports_tool_use"] is True
        assert entry["capabilities"]["supports_streaming"] is True
        assert entry["capabilities"]["output_modalities"] == ["text"]
        assert entry["contextLength"] == 272_000
        assert entry["context_length"] == 272_000
        assert entry["maxOutputTokens"] == 128_000
        assert entry["max_output_tokens"] == 128_000

    resp_codex = await async_client.get("/backend-api/codex/models")
    assert resp_codex.status_code == 200
    codex_by_slug = {item["slug"]: item for item in resp_codex.json()["models"]}
    assert codex_by_slug["gpt-5.4"]["context_window"] == 272_000
    assert codex_by_slug["gpt-5.4"]["max_context_window"] == 1_000_000
    assert codex_by_slug["gpt-5.5"]["context_window"] == 272_000
    assert codex_by_slug["gpt-5.5"]["max_context_window"] == 272_000


@pytest.mark.asyncio
async def test_v1_models_does_not_promote_raw_max_context_window(async_client):
    registry = get_model_registry()
    models = [_make_upstream_model("gpt-custom", raw=_raw_with_max_context_window(900_000))]
    await registry.update({"pro": models})

    resp = await async_client.get("/v1/models")
    assert resp.status_code == 200
    entry = next(item for item in resp.json()["data"] if item["id"] == "gpt-custom")

    assert entry["metadata"]["context_window"] == 272_000
    assert entry["metadata"]["input_context_window"] == 272_000
    assert entry["metadata"].get("max_output_tokens") is None
