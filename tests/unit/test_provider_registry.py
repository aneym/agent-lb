from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

from app.core.anthropic.oauth import build_anthropic_authorization_url
from app.core.clients.oauth import build_authorization_url
from app.core.providers import (
    ANTHROPIC_PROVIDER_NAME,
    GLM_DEFAULT_PLAN,
    GLM_PROVIDER_NAME,
    OPENAI_PROVIDER_NAME,
    ProviderLookupError,
    get_provider,
    list_provider_names,
)

pytestmark = pytest.mark.unit


def test_openai_provider_is_default_registered_provider() -> None:
    provider = get_provider()

    assert provider.name == OPENAI_PROVIDER_NAME
    assert provider.requires_id_token is True
    assert list_provider_names() == (ANTHROPIC_PROVIDER_NAME, GLM_PROVIDER_NAME, OPENAI_PROVIDER_NAME)
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
