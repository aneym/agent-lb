from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.core.anthropic.oauth import build_anthropic_authorization_url, exchange_anthropic_authorization_code
from app.core.clients.google_oauth import (
    build_google_authorization_url,
    exchange_google_authorization_code,
    google_client_credentials,
)
from app.core.clients.oauth import OAuthTokens
from app.core.providers import ANTHROPIC_PROVIDER_NAME, GEMINI_PROVIDER_NAME, ProviderOAuthConfig


class ProviderOAuthFlow(Protocol):
    @property
    def state_token_bytes(self) -> int: ...

    def authorization_url(self, config: ProviderOAuthConfig, *, state: str, code_challenge: str) -> str: ...

    async def exchange_code(
        self, config: ProviderOAuthConfig, *, code: str, code_verifier: str, state: str | None
    ) -> OAuthTokens: ...


@dataclass(frozen=True, slots=True)
class AnthropicOAuthFlow:
    state_token_bytes: int = 32

    def authorization_url(self, config: ProviderOAuthConfig, *, state: str, code_challenge: str) -> str:
        return build_anthropic_authorization_url(
            state=state,
            code_challenge=code_challenge,
            authorize_url=config.authorize_url,
            client_id=config.client_id,
            redirect_uri=config.redirect_uri,
            scope=config.scope,
            extra_params=config.authorization_extra_params,
        )

    async def exchange_code(
        self, config: ProviderOAuthConfig, *, code: str, code_verifier: str, state: str | None
    ) -> OAuthTokens:
        return await exchange_anthropic_authorization_code(
            code=code,
            code_verifier=code_verifier,
            state=state,
            redirect_uri=config.redirect_uri,
            token_url=config.token_url,
            client_id=config.client_id,
        )


@dataclass(frozen=True, slots=True)
class GoogleOAuthFlow:
    state_token_bytes: int = 16

    def authorization_url(self, config: ProviderOAuthConfig, *, state: str, code_challenge: str) -> str:
        assert config.authorize_url is not None
        return build_google_authorization_url(
            authorize_url=config.authorize_url,
            client_id=config.client_id,
            redirect_uri=config.redirect_uri,
            scope=config.scope,
            state=state,
            code_challenge=code_challenge,
        )

    async def exchange_code(
        self, config: ProviderOAuthConfig, *, code: str, code_verifier: str, state: str | None
    ) -> OAuthTokens:
        assert config.token_url is not None
        return await exchange_google_authorization_code(
            code=code,
            code_verifier=code_verifier,
            redirect_uri=config.redirect_uri,
            token_url=config.token_url,
            credentials=google_client_credentials(),
        )


PROVIDER_OAUTH_FLOWS: dict[str, ProviderOAuthFlow] = {
    ANTHROPIC_PROVIDER_NAME: AnthropicOAuthFlow(),
    GEMINI_PROVIDER_NAME: GoogleOAuthFlow(),
}
