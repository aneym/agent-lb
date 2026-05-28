from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

from app.core.clients.oauth import build_authorization_url
from app.core.providers import OPENAI_PROVIDER_NAME, ProviderLookupError, get_provider, list_provider_names

pytestmark = pytest.mark.unit


def test_openai_provider_is_default_registered_provider() -> None:
    provider = get_provider()

    assert provider.name == OPENAI_PROVIDER_NAME
    assert provider.requires_id_token is True
    assert list_provider_names() == (OPENAI_PROVIDER_NAME,)
    assert provider.model_registry is not None
    assert provider.pricing is not None
    assert provider.sse_parser is not None
    assert provider.request_normalizer is not None


def test_unknown_provider_is_rejected() -> None:
    with pytest.raises(ProviderLookupError):
        get_provider("anthropic")


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
