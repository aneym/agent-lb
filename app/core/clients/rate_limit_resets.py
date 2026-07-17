"""Upstream client for ChatGPT saved rate-limit reset credits.

Endpoint contract mirrors the open-source Codex CLI backend client
(``codex-rs/backend-client/src/client/rate_limit_resets.rs``):

- ``GET  {base}/backend-api/wham/rate-limit-reset-credits``
- ``POST {base}/backend-api/wham/rate-limit-reset-credits/consume``

Calls are pinned to a single account's credentials and sent directly
upstream, matching the account probe precedent in
``app/modules/accounts/probes.py``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Literal

import aiohttp
from pydantic import BaseModel, ConfigDict, ValidationError

from app.core.clients.http import lease_http_session
from app.core.config.settings import get_settings
from app.core.types import JsonObject

logger = logging.getLogger(__name__)

RESET_CREDITS_TIMEOUT_SECONDS = 30.0
RESET_CREDITS_CONNECT_TIMEOUT_SECONDS = 10.0

ConsumeResetCreditCode = Literal["reset", "nothing_to_reset", "no_credit", "already_redeemed"]


class ResetCreditDetails(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    reset_type: str
    status: str
    granted_at: str
    expires_at: str | None = None
    title: str | None = None
    description: str | None = None


class ResetCreditsPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    credits: list[ResetCreditDetails]
    available_count: int


class ConsumeResetCreditPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    code: ConsumeResetCreditCode
    windows_reset: int = 0


class ResetCreditsError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


async def fetch_reset_credits(
    *,
    access_token: str,
    chatgpt_account_id: str | None,
) -> ResetCreditsPayload:
    data = await _request("GET", _credits_url(), access_token, chatgpt_account_id, body=None)
    try:
        return ResetCreditsPayload.model_validate(data)
    except ValidationError as exc:
        raise ResetCreditsError(502, "Invalid reset-credits payload") from exc


async def consume_reset_credit(
    *,
    access_token: str,
    chatgpt_account_id: str | None,
    redeem_request_id: str,
    credit_id: str | None = None,
) -> ConsumeResetCreditPayload:
    body: JsonObject = {"redeem_request_id": redeem_request_id}
    if credit_id is not None:
        body["credit_id"] = credit_id
    data = await _request("POST", _consume_url(), access_token, chatgpt_account_id, body=body)
    try:
        return ConsumeResetCreditPayload.model_validate(data)
    except ValidationError as exc:
        raise ResetCreditsError(502, "Invalid reset-credit consume payload") from exc


async def _request(
    method: str,
    url: str,
    access_token: str,
    chatgpt_account_id: str | None,
    *,
    body: JsonObject | None,
) -> JsonObject:
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    if chatgpt_account_id and not chatgpt_account_id.startswith(("email_", "local_")):
        headers["chatgpt-account-id"] = chatgpt_account_id
    timeout = aiohttp.ClientTimeout(
        total=RESET_CREDITS_TIMEOUT_SECONDS,
        sock_connect=RESET_CREDITS_CONNECT_TIMEOUT_SECONDS,
    )
    try:
        async with lease_http_session() as session:
            async with session.request(method, url, headers=headers, json=body, timeout=timeout) as resp:
                data = await _safe_json(resp)
                if resp.status >= 400:
                    message = _error_message(data) or f"Reset-credits request failed ({resp.status})"
                    logger.warning(
                        "Reset-credits request failed method=%s status=%s message=%s",
                        method,
                        resp.status,
                        message,
                    )
                    raise ResetCreditsError(resp.status, message)
                return data
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        raise ResetCreditsError(0, f"Reset-credits request failed: {exc}") from exc


def _base_url() -> str:
    base = get_settings().upstream_base_url.rstrip("/")
    if "/backend-api" not in base:
        base = f"{base}/backend-api"
    return base


def _credits_url() -> str:
    return f"{_base_url()}/wham/rate-limit-reset-credits"


def _consume_url() -> str:
    return f"{_base_url()}/wham/rate-limit-reset-credits/consume"


async def _safe_json(resp: aiohttp.ClientResponse) -> JsonObject:
    try:
        data = await resp.json(content_type=None)
    except Exception:
        text = await resp.text()
        return {"error": {"message": text.strip()}}
    return data if isinstance(data, dict) else {"error": {"message": str(data)}}


def _error_message(payload: JsonObject) -> str | None:
    error = payload.get("error")
    if isinstance(error, dict):
        message = error.get("message") or error.get("error_description")
        return message if isinstance(message, str) else None
    if isinstance(error, str):
        return error
    message = payload.get("message")
    return message if isinstance(message, str) else None
