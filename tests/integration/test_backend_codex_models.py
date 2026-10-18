from __future__ import annotations

import pytest

from app.core.openai.model_registry import get_model_registry
from tests.integration.model_catalog_helpers import BOOTSTRAP_MODEL_SLUGS as BOOTSTRAP_MODEL_SLUGS
from tests.integration.model_catalog_helpers import (
    EXPECTED_BOOTSTRAP_MINIMAL_CLIENT_VERSIONS as EXPECTED_BOOTSTRAP_MINIMAL_CLIENT_VERSIONS,
)
from tests.integration.model_catalog_helpers import EXPECTED_CORE_MODEL_PLANS as EXPECTED_CORE_MODEL_PLANS
from tests.integration.model_catalog_helpers import _make_upstream_model as _make_upstream_model

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_backend_codex_models_uses_bootstrap_upstream_metadata(async_client):
    registry = get_model_registry()
    registry._snapshot = None

    resp = await async_client.get("/backend-api/codex/models")
    assert resp.status_code == 200
    entries = {entry["slug"]: entry for entry in resp.json()["models"]}

    assert set(entries) == set(EXPECTED_BOOTSTRAP_MINIMAL_CLIENT_VERSIONS)
    for slug, expected_version in EXPECTED_BOOTSTRAP_MINIMAL_CLIENT_VERSIONS.items():
        assert entries[slug]["minimal_client_version"] == expected_version

    gpt54 = entries["gpt-5.4"]
    assert gpt54["minimal_client_version"] == "0.98.0"
    assert gpt54["max_context_window"] == 1_000_000
    assert set(gpt54["available_in_plans"]) == EXPECTED_CORE_MODEL_PLANS

    mini = entries["gpt-5.4-mini"]
    assert mini["prefer_websockets"] is True
    assert mini["default_verbosity"] == "medium"
    assert mini["minimal_client_version"] == "0.98.0"
    assert {level["effort"] for level in mini["supported_reasoning_levels"]} == {"low", "medium", "high", "xhigh"}

    spark = entries["gpt-5.3-codex-spark"]
    assert spark["context_window"] == 128_000
    assert spark["input_modalities"] == ["text"]
    assert spark["default_reasoning_level"] == "high"
    assert spark["supported_in_api"] is False
    assert spark["minimal_client_version"] == "0.100.0"

    auto_review = entries["codex-auto-review"]
    assert auto_review["visibility"] == "hide"
    assert auto_review["shell_type"] == "shell_command"
    assert auto_review["max_context_window"] == 1_000_000
    assert auto_review["minimal_client_version"] == "0.98.0"
    assert set(auto_review["available_in_plans"]) == EXPECTED_CORE_MODEL_PLANS
    assert set(entries["gpt-5.3-codex"]["available_in_plans"]) == EXPECTED_CORE_MODEL_PLANS


@pytest.mark.asyncio
async def test_backend_codex_models_returns_format1(async_client):
    registry = get_model_registry()
    models = [
        _make_upstream_model(
            "gpt-5.3-codex",
            raw={
                "shell_type": "shell_command",
                "visibility": "list",
                "availability_nux": None,
                "upgrade": {"model": "gpt-5.4", "migration_markdown": "Upgrade!"},
            },
        ),
        _make_upstream_model(
            "gpt-5.2",
            raw={
                "shell_type": "shell_command",
                "visibility": "list",
            },
        ),
    ]
    await registry.update({"plus": models, "pro": models})

    resp = await async_client.get("/backend-api/codex/models")
    assert resp.status_code == 200
    payload = resp.json()
    assert "models" in payload
    assert isinstance(payload["models"], list)
    slugs = {m["slug"] for m in payload["models"]}
    assert {"gpt-5.2", "gpt-5.3-codex"}.issubset(slugs)


@pytest.mark.asyncio
async def test_backend_codex_models_entry_has_upstream_fields(async_client):
    registry = get_model_registry()
    models = [
        _make_upstream_model(
            "gpt-5.3-codex",
            raw={
                "shell_type": "shell_command",
                "visibility": "list",
                "availability_nux": None,
                "upgrade": {"model": "gpt-5.4", "migration_markdown": "Upgrade!"},
            },
            base_instructions="You are a helpful coding assistant.",
        ),
    ]
    await registry.update({"plus": models, "pro": models})

    resp = await async_client.get("/backend-api/codex/models")
    assert resp.status_code == 200
    entries = resp.json()["models"]
    entry = next(m for m in entries if m["slug"] == "gpt-5.3-codex")

    assert entry["display_name"] == "gpt-5.3-codex"
    assert entry["description"] == "Test model gpt-5.3-codex"
    assert entry["base_instructions"] == "You are a helpful coding assistant."
    assert entry["context_window"] == 272000
    assert entry["supported_in_api"] is True
    assert entry["shell_type"] == "shell_command"
    assert entry["visibility"] == "list"
    assert entry["availability_nux"] is None
    assert entry["upgrade"] == {"model": "gpt-5.4", "migration_markdown": "Upgrade!"}


@pytest.mark.asyncio
async def test_backend_codex_models_preserves_upstream_visibility(async_client):
    registry = get_model_registry()
    models = [
        _make_upstream_model(
            "gpt-5.3-codex",
            raw={
                "shell_type": "shell_command",
                "visibility": "hide",
            },
        ),
    ]
    await registry.update({"plus": models, "pro": models})

    resp = await async_client.get("/backend-api/codex/models")
    assert resp.status_code == 200
    entries = resp.json()["models"]
    entry = next(m for m in entries if m["slug"] == "gpt-5.3-codex")
    assert entry["visibility"] == "hide"


@pytest.mark.asyncio
async def test_backend_codex_models_filters_disallowed_models(async_client):
    registry = get_model_registry()
    models = [
        _make_upstream_model("gpt-5.2", base_instructions="allowed"),
        _make_upstream_model("gpt-5.3-codex", base_instructions="blocked"),
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
            "name": "codex-restricted",
            "allowedModels": ["gpt-5.2"],
        },
    )
    assert created.status_code == 200
    key = created.json()["key"]

    resp = await async_client.get("/backend-api/codex/models", headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 200
    entries = resp.json()["models"]
    assert [entry["slug"] for entry in entries] == ["gpt-5.2"]
    assert entries[0]["base_instructions"] == "allowed"


@pytest.mark.asyncio
async def test_backend_codex_models_rewrites_visibility_when_opted_in(async_client):
    registry = get_model_registry()
    models = [
        _make_upstream_model(
            "gpt-5.2",
            raw={
                "shell_type": "shell_command",
                "visibility": "hide",
            },
        ),
        _make_upstream_model(
            "gpt-5.3-codex",
            raw={
                "shell_type": "shell_command",
                "visibility": "list",
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
            "name": "codex-visibility",
            "allowedModels": ["gpt-5.2"],
            "applyToCodexModel": True,
        },
    )
    assert created.status_code == 200
    key = created.json()["key"]

    resp = await async_client.get("/backend-api/codex/models", headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 200

    entries = {entry["slug"]: entry for entry in resp.json()["models"]}
    assert set(entries) == {"gpt-5.2", "gpt-5.3-codex"}
    assert entries["gpt-5.2"]["visibility"] == "list"
    assert entries["gpt-5.3-codex"]["visibility"] == "hide"


@pytest.mark.asyncio
async def test_backend_codex_models_visibility_allowlist_respects_enforced_model(async_client):
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
            "gpt-5.3-codex",
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
            "name": "codex-visibility-enforced",
            "allowedModels": ["gpt-5.2", "gpt-5.3-codex"],
            "applyToCodexModel": True,
            "enforcedModel": "gpt-5.3-codex",
        },
    )
    assert created.status_code == 200
    key = created.json()["key"]

    resp = await async_client.get("/backend-api/codex/models", headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 200

    entries = {entry["slug"]: entry for entry in resp.json()["models"]}
    assert set(entries) == {"gpt-5.2", "gpt-5.3-codex"}
    assert entries["gpt-5.2"]["visibility"] == "hide"
    assert entries["gpt-5.3-codex"]["visibility"] == "list"


@pytest.mark.asyncio
async def test_backend_codex_models_preserves_original_flow_without_allowlist(async_client):
    registry = get_model_registry()
    models = [
        _make_upstream_model(
            "gpt-5.2",
            raw={
                "shell_type": "shell_command",
                "visibility": "hide",
            },
        ),
        _make_upstream_model(
            "gpt-5.3-codex",
            raw={
                "shell_type": "shell_command",
                "visibility": "list",
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
            "name": "codex-visibility-no-allowlist",
            "applyToCodexModel": True,
        },
    )
    assert created.status_code == 200
    key = created.json()["key"]

    resp = await async_client.get("/backend-api/codex/models", headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 200

    entries = {entry["slug"]: entry for entry in resp.json()["models"]}
    assert set(entries) == {"gpt-5.2", "gpt-5.3-codex"}
    assert entries["gpt-5.2"]["visibility"] == "hide"
    assert entries["gpt-5.3-codex"]["visibility"] == "list"


@pytest.mark.asyncio
async def test_backend_codex_models_includes_supported_in_api_false_models(async_client):
    registry = get_model_registry()
    models = [
        _make_upstream_model("gpt-5.2"),
        _make_upstream_model("gpt-5.3-codex"),
        _make_upstream_model("gpt-hidden", supported_in_api=False),
    ]
    await registry.update({"plus": models, "pro": models})

    resp = await async_client.get("/backend-api/codex/models")
    assert resp.status_code == 200
    slugs = {m["slug"] for m in resp.json()["models"]}
    assert {"gpt-5.2", "gpt-5.3-codex", "gpt-hidden"}.issubset(slugs)


@pytest.mark.asyncio
async def test_backend_codex_models_uses_bootstrap_models_when_registry_not_populated(async_client):
    registry = get_model_registry()
    registry._snapshot = None
    resp = await async_client.get("/backend-api/codex/models")
    assert resp.status_code == 200
    payload = resp.json()
    slugs = {item["slug"] for item in payload["models"]}
    assert slugs == BOOTSTRAP_MODEL_SLUGS
    assert "gpt-5.5-pro" not in slugs
    assert all(not slug.startswith("gpt-image-") for slug in slugs)
