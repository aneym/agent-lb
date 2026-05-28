from __future__ import annotations

from dataclasses import dataclass

import aiohttp

from app.core.anthropic import model_registry, models, parsing
from app.core.anthropic import pricing as anthropic_pricing
from app.core.anthropic.oauth import (
    ANTHROPIC_DEFAULT_PLAN,
    ANTHROPIC_PROVIDER_NAME,
    refresh_anthropic_access_token,
)
from app.core.auth.refresh import TokenRefreshResult
from app.core.config.settings import get_settings
from app.core.providers.types import AccountMetadata, ProviderOAuthConfig


@dataclass(frozen=True, slots=True)
class AnthropicProvider:
    name: str = ANTHROPIC_PROVIDER_NAME
    requires_id_token: bool = False
    model_registry: object = model_registry
    pricing: object = anthropic_pricing
    sse_parser: object = parsing
    request_normalizer: object = models

    def oauth_config(self) -> ProviderOAuthConfig:
        settings = get_settings()
        return ProviderOAuthConfig(
            auth_base_url=settings.anthropic_auth_base_url,
            client_id=settings.anthropic_oauth_client_id,
            redirect_uri=settings.anthropic_oauth_redirect_uri,
            scope=settings.anthropic_oauth_scope,
            originator=None,
            authorization_extra_params={},
            requires_id_token=self.requires_id_token,
            authorize_url=settings.anthropic_oauth_authorize_url,
            token_url=settings.anthropic_oauth_token_url,
        )

    async def refresh_access_token(
        self,
        refresh_token: str,
        *,
        session: aiohttp.ClientSession | None = None,
    ) -> TokenRefreshResult:
        oauth_config = self.oauth_config()
        return await refresh_anthropic_access_token(
            refresh_token,
            token_url=oauth_config.token_url,
            client_id=oauth_config.client_id,
            session=session,
        )

    def account_metadata_from_id_token(self, id_token: str | None) -> AccountMetadata:
        return AccountMetadata(account_id=None, email=None, plan_type=ANTHROPIC_DEFAULT_PLAN)
