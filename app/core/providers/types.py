from __future__ import annotations

from collections.abc import Callable, Mapping
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
    authorize_url: str | None = None
    token_url: str | None = None


class Provider(Protocol):
    @property
    def name(self) -> str: ...
    @property
    def requires_id_token(self) -> bool: ...
    @property
    def model_registry(self) -> object: ...
    @property
    def pricing(self) -> object: ...
    @property
    def sse_parser(self) -> object: ...
    @property
    def request_normalizer(self) -> object: ...
    @property
    def access_token_refresh_interval_seconds(self) -> int | None: ...

    def oauth_config(self) -> ProviderOAuthConfig: ...

    async def refresh_access_token(
        self,
        refresh_token: str,
        *,
        session: aiohttp.ClientSession | None = None,
        on_exchange_start: Callable[[], None] | None = None,
    ) -> TokenRefreshResult: ...

    def account_metadata_from_id_token(self, id_token: str | None) -> AccountMetadata: ...
