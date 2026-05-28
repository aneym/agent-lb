from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote, urlencode

import aiohttp
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, ValidationError

from app.core.auth.refresh import RefreshError, TokenRefreshResult
from app.core.clients.http import lease_http_session
from app.core.clients.oauth import OAuthError, OAuthTokens
from app.core.config.settings import get_settings
from app.core.types import JsonObject
from app.core.utils.request_id import get_request_id

logger = logging.getLogger(__name__)

ANTHROPIC_PROVIDER_NAME = "anthropic"
ANTHROPIC_OAUTH_BETA = "oauth-2025-04-20"
ANTHROPIC_DEFAULT_PLAN = "claude"


class AnthropicOAuthTokenPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    access_token: StrictStr | None = None
    refresh_token: StrictStr | None = None
    expires_in: StrictInt | None = None
    token_type: StrictStr | None = None
    scope: StrictStr | None = None
    account_id: StrictStr | None = Field(default=None, validation_alias="account_id")
    user_id: StrictStr | None = None
    organization_id: StrictStr | None = None
    workspace_id: StrictStr | None = None
    email: StrictStr | None = None
    plan_type: StrictStr | None = None
    profile: JsonObject | None = None
    account: JsonObject | None = None
    user: JsonObject | None = None
    error: JsonObject | StrictStr | None = None
    error_description: StrictStr | None = None
    message: StrictStr | None = None
    error_code: StrictStr | None = None
    code: StrictStr | None = None
    status: StrictStr | None = None


def build_anthropic_authorization_url(
    *,
    state: str,
    code_challenge: str,
    authorize_url: str | None = None,
    client_id: str | None = None,
    redirect_uri: str | None = None,
    scope: str | None = None,
    extra_params: Mapping[str, str] | None = None,
) -> str:
    settings = get_settings()
    params = {
        "response_type": "code",
        "client_id": client_id or settings.anthropic_oauth_client_id,
        "redirect_uri": redirect_uri or settings.anthropic_oauth_redirect_uri,
        "scope": scope or settings.anthropic_oauth_scope,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    params.update(extra_params or {})
    authorize_endpoint = (authorize_url or settings.anthropic_oauth_authorize_url).rstrip("?")
    return f"{authorize_endpoint}?{urlencode(params, quote_via=quote)}"


async def exchange_anthropic_authorization_code(
    *,
    code: str,
    code_verifier: str,
    redirect_uri: str | None = None,
    token_url: str | None = None,
    client_id: str | None = None,
    timeout_seconds: float | None = None,
    session: aiohttp.ClientSession | None = None,
) -> OAuthTokens:
    settings = get_settings()
    payload = {
        "grant_type": "authorization_code",
        "client_id": client_id or settings.anthropic_oauth_client_id,
        "code": code,
        "code_verifier": code_verifier,
        "redirect_uri": redirect_uri or settings.anthropic_oauth_redirect_uri,
    }
    payload_data = await _post_token_request(
        token_url=token_url or settings.anthropic_oauth_token_url,
        payload=payload,
        timeout_seconds=timeout_seconds,
        session=session,
        error_prefix="OAuth",
    )
    if not payload_data.access_token or not payload_data.refresh_token:
        raise OAuthError("invalid_response", "OAuth response missing tokens")

    metadata = _metadata_from_payload(payload_data)
    return OAuthTokens(
        access_token=payload_data.access_token,
        refresh_token=payload_data.refresh_token,
        id_token=None,
        account_id=metadata.account_id,
        email=metadata.email,
        plan_type=metadata.plan_type,
    )


async def refresh_anthropic_access_token(
    refresh_token: str,
    *,
    token_url: str | None = None,
    client_id: str | None = None,
    session: aiohttp.ClientSession | None = None,
) -> TokenRefreshResult:
    settings = get_settings()
    payload = {
        "grant_type": "refresh_token",
        "client_id": client_id or settings.anthropic_oauth_client_id,
        "refresh_token": refresh_token,
        "scope": settings.anthropic_oauth_scope,
    }
    try:
        payload_data = await _post_token_request(
            token_url=token_url or settings.anthropic_oauth_token_url,
            payload=payload,
            timeout_seconds=settings.token_refresh_timeout_seconds,
            session=session,
            error_prefix="Token refresh",
        )
    except OAuthError as exc:
        raise RefreshError(exc.code, exc.message, False, transport_error=exc.code == "transport_error") from exc

    if not payload_data.access_token or not payload_data.refresh_token:
        raise RefreshError("invalid_response", "Refresh response missing tokens", False)

    metadata = _metadata_from_payload(payload_data)
    return TokenRefreshResult(
        access_token=payload_data.access_token,
        refresh_token=payload_data.refresh_token,
        id_token=None,
        account_id=metadata.account_id,
        plan_type=metadata.plan_type,
        email=metadata.email,
    )


class AnthropicTokenMetadata(BaseModel):
    account_id: str | None
    email: str | None
    plan_type: str | None


async def _post_token_request(
    *,
    token_url: str,
    payload: Mapping[str, str],
    timeout_seconds: float | None,
    session: aiohttp.ClientSession | None,
    error_prefix: str,
) -> AnthropicOAuthTokenPayload:
    settings = get_settings()
    encoded = urlencode(payload, quote_via=quote)
    timeout = aiohttp.ClientTimeout(total=timeout_seconds or settings.oauth_timeout_seconds)
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "anthropic-beta": ANTHROPIC_OAUTH_BETA,
    }
    request_id = get_request_id()
    if request_id:
        headers["x-request-id"] = request_id
    try:
        async with lease_http_session(session) as client_session:
            async with client_session.post(token_url, data=encoded, headers=headers, timeout=timeout) as resp:
                data = await _safe_json(resp)
                try:
                    payload_data = AnthropicOAuthTokenPayload.model_validate(data)
                except ValidationError as exc:
                    logger.warning(
                        "Anthropic token response invalid request_id=%s",
                        request_id,
                    )
                    raise OAuthError("invalid_response", f"{error_prefix} response invalid") from exc
                if resp.status >= 400:
                    logger.warning(
                        "Anthropic token request failed request_id=%s status=%s",
                        request_id,
                        resp.status,
                    )
                    raise _oauth_error_from_payload(payload_data, resp.status, prefix=error_prefix)
    except OAuthError:
        raise
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
        message = str(exc) or exc.__class__.__name__
        raise OAuthError("transport_error", f"Transport error during {error_prefix.lower()}: {message}") from exc
    return payload_data


async def _safe_json(resp: aiohttp.ClientResponse) -> JsonObject:
    try:
        data = await resp.json(content_type=None)
    except Exception:
        text = await resp.text()
        return {"error": {"message": text.strip()}}
    return data if isinstance(data, dict) else {"error": {"message": str(data)}}


def _oauth_error_from_payload(
    payload: AnthropicOAuthTokenPayload,
    status_code: int,
    *,
    prefix: str,
) -> OAuthError:
    code = _extract_error_code(payload) or f"http_{status_code}"
    message = _extract_error_message(payload) or f"{prefix} request failed ({status_code})"
    return OAuthError(code, message, status_code)


def _extract_error_code(payload: AnthropicOAuthTokenPayload) -> str | None:
    error = payload.error
    if isinstance(error, dict):
        code = error.get("code") or error.get("error")
        return code if isinstance(code, str) else None
    if isinstance(error, str):
        return error
    return payload.error_code or payload.code


def _extract_error_message(payload: AnthropicOAuthTokenPayload) -> str | None:
    error = payload.error
    if isinstance(error, dict):
        message = error.get("message") or error.get("error_description")
        return message if isinstance(message, str) else None
    if isinstance(error, str):
        return payload.error_description or error
    return payload.message


def _metadata_from_payload(payload: AnthropicOAuthTokenPayload) -> AnthropicTokenMetadata:
    metadata = _payload_dict(payload)
    account_id = _first_text(
        payload.account_id,
        payload.user_id,
        payload.organization_id,
        payload.workspace_id,
        _nested_text(metadata, ("account", "id")),
        _nested_text(metadata, ("account", "uuid")),
        _nested_text(metadata, ("user", "id")),
        _nested_text(metadata, ("profile", "id")),
        _nested_text(metadata, ("profile", "email")),
        payload.email,
    )
    email = _first_text(
        payload.email,
        _nested_text(metadata, ("account", "email")),
        _nested_text(metadata, ("user", "email")),
        _nested_text(metadata, ("profile", "email")),
    )
    if account_id is None:
        account_id = _stable_account_id(payload)
    return AnthropicTokenMetadata(
        account_id=account_id,
        email=email,
        plan_type=_first_text(payload.plan_type, _nested_text(metadata, ("account", "plan_type")))
        or ANTHROPIC_DEFAULT_PLAN,
    )


def _payload_dict(payload: AnthropicOAuthTokenPayload) -> dict[str, Any]:
    return payload.model_dump(mode="python", exclude_none=True)


def _nested_text(source: Mapping[str, Any], path: tuple[str, ...]) -> str | None:
    current: Any = source
    for key in path:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current if isinstance(current, str) and current else None


def _first_text(*values: str | None) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _stable_account_id(payload: AnthropicOAuthTokenPayload) -> str:
    material = payload.refresh_token or payload.access_token or repr(_payload_dict(payload))
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]
    return f"{ANTHROPIC_PROVIDER_NAME}_{digest}"
