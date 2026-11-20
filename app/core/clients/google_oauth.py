from __future__ import annotations

import asyncio
import base64
import json
import logging
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import urlencode

import aiohttp
from pydantic import BaseModel, ConfigDict, StrictInt, StrictStr, ValidationError

from app.core.auth.exchange_phase import ExchangePhase, aiohttp_exchange_phase
from app.core.auth.refresh import RefreshError, TokenRefreshResult, classify_refresh_error
from app.core.clients.http import lease_http_session
from app.core.clients.oauth import OAuthError, OAuthTokens
from app.core.config.settings import get_settings

logger = logging.getLogger(__name__)

GOOGLE_OAUTH_ERROR_CODES = frozenset(
    {
        "invalid_grant",
        "invalid_client",
        "invalid_request",
        "invalid_scope",
        "unauthorized_client",
        "unsupported_grant_type",
        "access_denied",
    }
)


@dataclass(frozen=True, slots=True)
class GoogleClientCredentials:
    client_id: str
    client_secret: str


@dataclass(frozen=True, slots=True)
class GoogleIdentity:
    subject: str | None
    email: str | None


class GoogleTokenPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    access_token: StrictStr | None = None
    refresh_token: StrictStr | None = None
    id_token: StrictStr | None = None
    expires_in: StrictInt | None = None
    error: StrictStr | None = None


@lru_cache(maxsize=4)
def _agent_secret(name: str) -> str:
    try:
        found = subprocess.run(["agent-secret", "get", name], capture_output=True, text=True, timeout=20, check=True)
    except (OSError, subprocess.SubprocessError) as exc:
        raise OAuthError("gemini_client_unavailable", f"agent-secret {name} could not be read", 500) from exc
    value = found.stdout.strip()
    if not value:
        raise OAuthError("gemini_client_unavailable", f"agent-secret {name} is empty", 500)
    return value


def google_client_id() -> str:
    return _agent_secret(get_settings().gemini_oauth_client_id_secret_name)


def google_client_credentials() -> GoogleClientCredentials:
    settings = get_settings()
    return GoogleClientCredentials(
        client_id=_agent_secret(settings.gemini_oauth_client_id_secret_name),
        client_secret=_agent_secret(settings.gemini_oauth_client_secret_name),
    )


def google_identity(id_token: str | None) -> GoogleIdentity:
    if not id_token:
        return GoogleIdentity(subject=None, email=None)
    parts = id_token.split(".")
    if len(parts) < 2:
        return GoogleIdentity(subject=None, email=None)
    try:
        claims = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
    except (ValueError, UnicodeDecodeError):
        return GoogleIdentity(subject=None, email=None)
    if not isinstance(claims, dict):
        return GoogleIdentity(subject=None, email=None)
    subject = claims.get("sub")
    email = claims.get("email")
    return GoogleIdentity(
        subject=subject if isinstance(subject, str) and subject else None,
        email=email if isinstance(email, str) and email else None,
    )


def build_google_authorization_url(
    *,
    authorize_url: str,
    client_id: str,
    redirect_uri: str,
    scope: str,
    state: str,
    code_challenge: str,
) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": scope,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "access_type": "offline",
        "prompt": "consent",
    }
    return f"{authorize_url.rstrip('?')}?{urlencode(params)}"


async def exchange_google_authorization_code(
    *,
    code: str,
    code_verifier: str,
    redirect_uri: str,
    token_url: str,
    credentials: GoogleClientCredentials,
    session: aiohttp.ClientSession | None = None,
) -> OAuthTokens:
    payload = await _post_token_form(
        token_url=token_url,
        form={
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": code_verifier,
            "redirect_uri": redirect_uri,
            "client_id": credentials.client_id,
            "client_secret": credentials.client_secret,
        },
        session=session,
        error_prefix="Google sign-in",
    )
    if not payload.access_token:
        raise OAuthError("invalid_response", "Google sign-in response missing access token")
    if not payload.refresh_token:
        raise OAuthError(
            "missing_refresh_token",
            "Google returned no refresh token; remove agent-lb from the Google account's connected apps and sign in again",
        )
    return OAuthTokens(
        access_token=payload.access_token,
        refresh_token=payload.refresh_token,
        id_token=payload.id_token,
        expires_in=payload.expires_in,
    )


async def refresh_google_access_token(
    refresh_token: str,
    *,
    token_url: str,
    credentials: GoogleClientCredentials,
    session: aiohttp.ClientSession | None = None,
    on_exchange_start: Callable[[], None] | None = None,
) -> TokenRefreshResult:
    try:
        payload = await _post_token_form(
            token_url=token_url,
            form={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": credentials.client_id,
                "client_secret": credentials.client_secret,
            },
            session=session,
            error_prefix="Google token refresh",
            on_exchange_start=on_exchange_start,
        )
    except OAuthError as exc:
        raise RefreshError(
            exc.code,
            exc.message,
            classify_refresh_error(exc.code),
            transport_error=exc.code == "transport_error",
            phase=exc.phase,
            status_code=exc.status_code,
        ) from exc
    if not payload.access_token:
        raise RefreshError("invalid_response", "Google refresh response missing access token", False)
    identity = google_identity(payload.id_token)
    return TokenRefreshResult(
        access_token=payload.access_token,
        refresh_token=payload.refresh_token or refresh_token,
        id_token=payload.id_token,
        account_id=identity.subject,
        plan_type=None,
        email=identity.email,
        expires_in=payload.expires_in,
    )


async def google_access_token_rejected(access_token: str, *, session: aiohttp.ClientSession | None = None) -> bool:
    timeout = aiohttp.ClientTimeout(total=get_settings().oauth_timeout_seconds)
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    try:
        async with lease_http_session(session) as client_session:
            async with client_session.get(
                get_settings().gemini_oauth_userinfo_url, headers=headers, timeout=timeout, allow_redirects=False
            ) as resp:
                return resp.status == 401
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
        return False


async def _post_token_form(
    *,
    token_url: str,
    form: Mapping[str, str],
    session: aiohttp.ClientSession | None,
    error_prefix: str,
    on_exchange_start: Callable[[], None] | None = None,
) -> GoogleTokenPayload:
    timeout = aiohttp.ClientTimeout(total=get_settings().oauth_timeout_seconds)
    headers = {"Accept": "application/json"}
    exchange_started = False
    try:
        async with lease_http_session(session) as client_session:
            exchange_started = True
            if on_exchange_start:
                on_exchange_start()
            async with client_session.post(
                token_url, data=dict(form), headers=headers, timeout=timeout, allow_redirects=False
            ) as resp:
                try:
                    data = await resp.json(content_type=None)
                except (aiohttp.ContentTypeError, ValueError) as exc:
                    raise OAuthError(
                        f"http_{resp.status}",
                        f"{error_prefix} failed ({resp.status})",
                        resp.status,
                        phase=ExchangePhase.ANSWERED,
                    ) from exc
                try:
                    payload = GoogleTokenPayload.model_validate(data if isinstance(data, dict) else {})
                except ValidationError as exc:
                    raise OAuthError(
                        "invalid_response", f"{error_prefix} response invalid", phase=ExchangePhase.ANSWERED
                    ) from exc
                if resp.status >= 300:
                    code = payload.error if payload.error in GOOGLE_OAUTH_ERROR_CODES else f"http_{resp.status}"
                    logger.warning("Google token request failed status=%s code=%s", resp.status, code)
                    raise OAuthError(
                        code,
                        f"{error_prefix} failed ({resp.status} {code})",
                        resp.status,
                        phase=ExchangePhase.ANSWERED,
                    )
                return payload
    except OAuthError:
        raise
    except RuntimeError as exc:
        if not exchange_started:
            raise OAuthError("transport_error", "HTTP client unavailable", phase=ExchangePhase.PRE_SEND) from exc
        raise
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
        raise OAuthError(
            "transport_error",
            f"Transport error during {error_prefix.lower()}: {exc.__class__.__name__}",
            phase=aiohttp_exchange_phase(exc),
        ) from exc
