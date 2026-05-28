from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

import aiohttp

from app.core.auth.refresh import TokenRefreshResult


@dataclass(frozen=True, slots=True)
class AccountMetadata:
    account_id: str | None
    email: str | None
    plan_type: str | None


@dataclass(frozen=True, slots=True)
class ProviderOAuthConfig:
    auth_base_url: str
    client_id: str
    redirect_uri: str
    scope: str
    originator: str | None
    authorization_extra_params: Mapping[str, str]
    requires_id_token: bool


class Provider(Protocol):
    name: str
    requires_id_token: bool
    model_registry: object
    pricing: object
    sse_parser: object
    request_normalizer: object

    def oauth_config(self) -> ProviderOAuthConfig: ...

    async def refresh_access_token(
        self,
        refresh_token: str,
        *,
        session: aiohttp.ClientSession | None = None,
    ) -> TokenRefreshResult: ...

    def account_metadata_from_id_token(self, id_token: str | None) -> AccountMetadata: ...
