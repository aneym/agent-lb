from __future__ import annotations

import base64
import json

from app.core.auth import generate_unique_account_id
from app.core.openai.model_registry import ReasoningLevel, UpstreamModel, get_model_registry

_TEST_MODELS = ["model-alpha", "model-beta"]


_HIDDEN_MODEL = "model-hidden"


def _make_upstream_model(slug: str, *, supported_in_api: bool = True) -> UpstreamModel:
    return UpstreamModel(
        slug=slug,
        display_name=slug,
        description=f"Test model {slug}",
        context_window=128000,
        input_modalities=("text",),
        supported_reasoning_levels=(ReasoningLevel(effort="medium", description="default"),),
        default_reasoning_level="medium",
        supports_reasoning_summaries=False,
        support_verbosity=False,
        default_verbosity=None,
        prefer_websockets=False,
        supports_parallel_tool_calls=True,
        supported_in_api=supported_in_api,
        minimal_client_version=None,
        priority=0,
        available_in_plans=frozenset({"plus", "pro"}),
        raw={},
    )


async def _populate_test_registry() -> None:
    registry = get_model_registry()
    models = [_make_upstream_model(slug) for slug in _TEST_MODELS]
    await registry.update({"plus": models, "pro": models})


def _encode_jwt(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return f"header.{body}.sig"


def _make_auth_json(account_id: str, email: str) -> dict:
    payload = {
        "email": email,
        "chatgpt_account_id": account_id,
        "https://api.openai.com/auth": {"chatgpt_plan_type": "plus"},
    }
    return {
        "tokens": {
            "idToken": _encode_jwt(payload),
            "accessToken": "access-token",
            "refreshToken": "refresh-token",
            "accountId": account_id,
        },
    }


async def _import_account(async_client, account_id: str, email: str) -> str:
    auth_json = _make_auth_json(account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200
    return generate_unique_account_id(account_id, email)
