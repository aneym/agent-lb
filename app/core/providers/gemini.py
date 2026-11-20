from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import aiohttp

from app.core.auth.exchange_phase import ExchangePhase
from app.core.auth.refresh import RefreshError, TokenRefreshResult
from app.core.clients.google_oauth import (
    google_client_credentials,
    google_client_id,
    google_identity,
    refresh_google_access_token,
)
from app.core.clients.oauth import OAuthError
from app.core.config.settings import get_settings
from app.core.providers.gemini_models import GEMINI_MODEL_REGISTRY
from app.core.providers.types import AccountMetadata, ProviderOAuthConfig

GEMINI_PROVIDER_NAME = "gemini"
GEMINI_DEFAULT_PLAN = "gemini"
GEMINI_ACCESS_TOKEN_REFRESH_INTERVAL_SECONDS = 45 * 60


@dataclass(frozen=True, slots=True)
class GeminiProvider:
    name: str = GEMINI_PROVIDER_NAME
    requires_id_token: bool = False
    model_registry: object = GEMINI_MODEL_REGISTRY
    pricing: object = None
    sse_parser: object = None
    request_normalizer: object = None
    access_token_refresh_interval_seconds: int | None = GEMINI_ACCESS_TOKEN_REFRESH_INTERVAL_SECONDS

    def oauth_config(self) -> ProviderOAuthConfig:
        settings = get_settings()
        return ProviderOAuthConfig(
            auth_base_url=settings.gemini_oauth_authorize_url,
            client_id=google_client_id(),
            redirect_uri=settings.gemini_oauth_redirect_uri,
            scope=settings.gemini_oauth_scope,
            originator=None,
            authorization_extra_params={},
            requires_id_token=self.requires_id_token,
            authorize_url=settings.gemini_oauth_authorize_url,
            token_url=settings.gemini_oauth_token_url,
        )

    async def refresh_access_token(
        self,
        refresh_token: str,
        *,
        session: aiohttp.ClientSession | None = None,
        on_exchange_start: Callable[[], None] | None = None,
    ) -> TokenRefreshResult:
        try:
            credentials = google_client_credentials()
        except OAuthError as exc:
            raise RefreshError(exc.code, exc.message, False, phase=ExchangePhase.PRE_SEND) from exc
        return await refresh_google_access_token(
            refresh_token,
            token_url=get_settings().gemini_oauth_token_url,
            credentials=credentials,
            session=session,
            on_exchange_start=on_exchange_start,
        )

    def account_metadata_from_id_token(self, id_token: str | None) -> AccountMetadata:
        identity = google_identity(id_token)
        return AccountMetadata(account_id=identity.subject, email=identity.email, plan_type=GEMINI_DEFAULT_PLAN)
