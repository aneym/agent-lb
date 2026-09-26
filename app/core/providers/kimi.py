from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TypedDict

import aiohttp

from app.core.anthropic import model_registry, models, parsing
from app.core.anthropic import pricing as anthropic_pricing
from app.core.auth.refresh import RefreshError, TokenRefreshResult
from app.core.clients.http import lease_http_session
from app.core.config.settings import get_settings
from app.core.providers.anthropic_compat import echo_api_key_refresh
from app.core.providers.types import AccountMetadata, ProviderOAuthConfig

KIMI_PROVIDER_NAME = "kimi"
KIMI_DEFAULT_PLAN = "kimi-coding"
KIMI_ACCESS_TOKEN_REFRESH_INTERVAL_SECONDS = 30 * 60


class KimiTokenResponse(TypedDict, total=False):
    access_token: str
    refresh_token: str
    expires_in: int
    scope: str
    token_type: str


@dataclass(frozen=True, slots=True)
class KimiProvider:
    name: str = KIMI_PROVIDER_NAME
    requires_id_token: bool = False
    model_registry: object = model_registry
    pricing: object = anthropic_pricing
    sse_parser: object = parsing
    request_normalizer: object = models
    access_token_refresh_interval_seconds: int | None = KIMI_ACCESS_TOKEN_REFRESH_INTERVAL_SECONDS

    def oauth_config(self) -> ProviderOAuthConfig:
        raise NotImplementedError("Kimi accounts are imported with credentials, not interactive OAuth")

    async def refresh_access_token(
        self,
        refresh_token: str,
        *,
        session: aiohttp.ClientSession | None = None,
    ) -> TokenRefreshResult:
        if refresh_token.startswith("sk-"):
            return echo_api_key_refresh(refresh_token, plan_type=KIMI_DEFAULT_PLAN)

        settings = get_settings()
        payload = {
            "client_id": settings.kimi_oauth_client_id,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }
        timeout = aiohttp.ClientTimeout(total=30)
        try:
            async with lease_http_session(session) as client_session:
                async with client_session.post(
                    settings.kimi_oauth_token_url,
                    data=payload,
                    timeout=timeout,
                ) as response:
                    data = await response.json(content_type=None)
                    if response.status >= 400:
                        raise RefreshError(
                            f"http_{response.status}",
                            f"Kimi token refresh failed ({response.status})",
                            response.status in {400, 401, 403},
                        )
        except RefreshError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError) as exc:
            message = str(exc) or exc.__class__.__name__
            raise RefreshError(
                "transport_error",
                f"Transport error during Kimi token refresh: {message}",
                False,
                transport_error=True,
            ) from exc

        if not isinstance(data, dict):
            raise RefreshError("invalid_response", "Kimi token refresh response must be an object", False)
        token_data: KimiTokenResponse = data
        access_token = token_data.get("access_token")
        rotated_refresh_token = token_data.get("refresh_token")
        if not isinstance(access_token, str) or not access_token:
            raise RefreshError("invalid_response", "Kimi token refresh response missing access_token", False)
        if not isinstance(rotated_refresh_token, str) or not rotated_refresh_token:
            raise RefreshError("invalid_response", "Kimi token refresh response missing refresh_token", False)

        return TokenRefreshResult(
            access_token=access_token,
            refresh_token=rotated_refresh_token,
            id_token=None,
            account_id=None,
            plan_type=KIMI_DEFAULT_PLAN,
            email=None,
        )

    def account_metadata_from_id_token(self, id_token: str | None) -> AccountMetadata:
        del id_token
        return AccountMetadata(account_id=None, email=None, plan_type=KIMI_DEFAULT_PLAN)
