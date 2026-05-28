from __future__ import annotations

from dataclasses import dataclass

import aiohttp

from app.core.auth import DEFAULT_PLAN, OpenAIAuthClaims, extract_id_token_claims
from app.core.auth.refresh import TokenRefreshResult, refresh_access_token
from app.core.config.settings import get_settings
from app.core.openai import model_registry, parsing, requests
from app.core.providers.types import AccountMetadata, ProviderOAuthConfig
from app.core.usage import pricing

OPENAI_PROVIDER_NAME = "openai"
OPENAI_AUTHORIZATION_EXTRA_PARAMS = {
    "id_token_add_organizations": "true",
    "codex_cli_simplified_flow": "true",
}


@dataclass(frozen=True, slots=True)
class OpenAIProvider:
    name: str = OPENAI_PROVIDER_NAME
    requires_id_token: bool = True
    model_registry: object = model_registry
    pricing: object = pricing
    sse_parser: object = parsing
    request_normalizer: object = requests
    access_token_refresh_interval_seconds: int | None = None

    def oauth_config(self) -> ProviderOAuthConfig:
        settings = get_settings()
        return ProviderOAuthConfig(
            auth_base_url=settings.auth_base_url,
            client_id=settings.oauth_client_id,
            redirect_uri=settings.oauth_redirect_uri,
            scope=settings.oauth_scope,
            originator=settings.oauth_originator,
            authorization_extra_params=OPENAI_AUTHORIZATION_EXTRA_PARAMS,
            requires_id_token=self.requires_id_token,
        )

    async def refresh_access_token(
        self,
        refresh_token: str,
        *,
        session: aiohttp.ClientSession | None = None,
    ) -> TokenRefreshResult:
        return await refresh_access_token(refresh_token, session=session)

    def account_metadata_from_id_token(self, id_token: str | None) -> AccountMetadata:
        if not id_token:
            return AccountMetadata(account_id=None, email=None, plan_type=None)
        claims = extract_id_token_claims(id_token)
        auth_claims = claims.auth or OpenAIAuthClaims()
        return AccountMetadata(
            account_id=auth_claims.chatgpt_account_id or claims.chatgpt_account_id,
            email=claims.email,
            plan_type=auth_claims.chatgpt_plan_type or claims.chatgpt_plan_type or DEFAULT_PLAN,
        )
