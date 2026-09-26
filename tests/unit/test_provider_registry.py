from __future__ import annotations

from typing import Any, cast
from urllib.parse import parse_qs, urlparse

import aiohttp
import pytest

from app.core.anthropic.oauth import build_anthropic_authorization_url
from app.core.clients.oauth import build_authorization_url
from app.core.providers import (
    ANTHROPIC_PROVIDER_NAME,
    GLM_DEFAULT_PLAN,
    GLM_PROVIDER_NAME,
    KIMI_DEFAULT_PLAN,
    KIMI_PROVIDER_NAME,
    OPENAI_PROVIDER_NAME,
    ProviderLookupError,
    get_anthropic_compat_profile,
    get_provider,
    list_provider_names,
    provider_name_for_anthropic_model,
)
from app.core.providers.openrouter import OPENROUTER_PROVIDER_NAME

pytestmark = pytest.mark.unit


class _FakeResponse:
    status = 200

    async def __aenter__(self) -> "_FakeResponse":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def json(self, *, content_type: str | None = None) -> dict[str, Any]:
        return {"access_token": "new-access", "refresh_token": "rotated-refresh", "expires_in": 1800}


class _FakeSession:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    def post(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.requests.append({"url": url, **kwargs})
        return _FakeResponse()


def test_openai_provider_is_default_registered_provider() -> None:
    provider = get_provider()

    assert provider.name == OPENAI_PROVIDER_NAME
    assert provider.requires_id_token is True
    assert list_provider_names() == (
        ANTHROPIC_PROVIDER_NAME,
        GLM_PROVIDER_NAME,
        KIMI_PROVIDER_NAME,
        OPENAI_PROVIDER_NAME,
        OPENROUTER_PROVIDER_NAME,
    )
    assert provider.model_registry is not None
    assert provider.pricing is not None
    assert provider.sse_parser is not None
    assert provider.request_normalizer is not None


def test_unknown_provider_is_rejected() -> None:
    with pytest.raises(ProviderLookupError):
        get_provider("unknown")


def test_openai_oauth_config_preserves_existing_authorization_query() -> None:
    provider = get_provider(OPENAI_PROVIDER_NAME)
    oauth_config = provider.oauth_config()

    url = build_authorization_url(
        state="state-token",
        code_challenge="challenge",
        base_url=oauth_config.auth_base_url,
        client_id=oauth_config.client_id,
        originator=oauth_config.originator,
        redirect_uri=oauth_config.redirect_uri,
        scope=oauth_config.scope,
        extra_params=oauth_config.authorization_extra_params,
    )

    params = parse_qs(urlparse(url).query)
    assert params["client_id"] == [oauth_config.client_id]
    assert params["scope"] == [oauth_config.scope]
    assert params["id_token_add_organizations"] == ["true"]
    assert params["codex_cli_simplified_flow"] == ["true"]


def test_anthropic_oauth_config_uses_claude_code_public_client_without_openai_params() -> None:
    provider = get_provider(ANTHROPIC_PROVIDER_NAME)
    oauth_config = provider.oauth_config()

    url = build_anthropic_authorization_url(
        state="state-token",
        code_challenge="challenge",
        authorize_url=oauth_config.authorize_url,
        client_id=oauth_config.client_id,
        redirect_uri=oauth_config.redirect_uri,
        scope=oauth_config.scope,
        extra_params=oauth_config.authorization_extra_params,
    )

    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    assert provider.requires_id_token is False
    assert parsed.scheme == "https"
    assert parsed.netloc == "claude.com"
    assert parsed.path == "/cai/oauth/authorize"
    assert parsed.query.startswith("code=true&client_id=9d1c250a-e61b-44d9-88ed-5944d1962f5e&response_type=code&")
    assert params["client_id"] == ["9d1c250a-e61b-44d9-88ed-5944d1962f5e"]
    assert params["code"] == ["true"]
    assert params["redirect_uri"] == ["https://platform.claude.com/oauth/code/callback"]
    assert params["scope"] == [
        "org:create_api_key user:profile user:inference user:sessions:claude_code user:mcp_servers user:file_upload"
    ]
    assert "id_token_add_organizations" not in params
    assert "codex_cli_simplified_flow" not in params


def test_glm_provider_is_api_key_based() -> None:
    provider = get_provider(GLM_PROVIDER_NAME)

    assert provider.name == GLM_PROVIDER_NAME
    assert provider.requires_id_token is False
    assert provider.account_metadata_from_id_token(None).plan_type == GLM_DEFAULT_PLAN
    with pytest.raises(NotImplementedError):
        provider.oauth_config()


def test_anthropic_compat_profiles_route_models_and_preserve_provider_metadata() -> None:
    assert provider_name_for_anthropic_model("  K3-256K ") == KIMI_PROVIDER_NAME
    assert provider_name_for_anthropic_model("kimi-k3") == KIMI_PROVIDER_NAME
    assert provider_name_for_anthropic_model("glm-5.2") == GLM_PROVIDER_NAME
    assert provider_name_for_anthropic_model("claude-fable-5") == ANTHROPIC_PROVIDER_NAME

    glm = get_anthropic_compat_profile(GLM_PROVIDER_NAME)
    kimi = get_anthropic_compat_profile(KIMI_PROVIDER_NAME)
    assert glm.count_tokens_quota_key == "glm_count_tokens"
    assert glm.sticky_prefix == "glm"
    assert kimi.default_probe_model == "kimi-k3"
    assert kimi.import_defaults is not None
    assert kimi.import_defaults.plan_type == KIMI_DEFAULT_PLAN


@pytest.mark.asyncio
async def test_kimi_provider_refreshes_oauth_tokens_and_persists_rotation() -> None:
    provider = get_provider(KIMI_PROVIDER_NAME)
    session = _FakeSession()

    result = await provider.refresh_access_token(
        "oauth-refresh",
        session=cast(aiohttp.ClientSession, session),
    )

    assert result.access_token == "new-access"
    assert result.refresh_token == "rotated-refresh"
    assert result.plan_type == KIMI_DEFAULT_PLAN
    assert len(session.requests) == 1
    request = session.requests[0]
    assert request["url"] == "https://auth.kimi.com/api/oauth/token"
    assert request["data"] == {
        "client_id": "17e5f671-d194-4dfb-9706-5516cb48c098",
        "grant_type": "refresh_token",
        "refresh_token": "oauth-refresh",
    }


@pytest.mark.asyncio
async def test_kimi_provider_echoes_static_api_key_without_network() -> None:
    provider = get_provider(KIMI_PROVIDER_NAME)
    session = _FakeSession()

    result = await provider.refresh_access_token(
        "sk-static-key",
        session=cast(aiohttp.ClientSession, session),
    )

    assert result.access_token == "sk-static-key"
    assert result.refresh_token == "sk-static-key"
    assert result.plan_type == KIMI_DEFAULT_PLAN
    assert session.requests == []
