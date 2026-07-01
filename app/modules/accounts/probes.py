from __future__ import annotations

import asyncio
import json
import logging
from enum import Enum
from urllib.parse import urljoin

import aiohttp

from app.core.anthropic.oauth import ANTHROPIC_OAUTH_BETA
from app.core.clients.http import lease_http_session
from app.core.config.settings import get_settings

logger = logging.getLogger(__name__)

DEFAULT_PROBE_MODEL = "gpt-5.5"
DEFAULT_ANTHROPIC_SUBSCRIPTION_CHECK_MODEL = "claude-haiku-4-5"
DEFAULT_GLM_PROBE_MODEL = "glm-5.2"
PROBE_REQUEST_TIMEOUT_SECONDS = 30.0
PROBE_CONNECT_TIMEOUT_SECONDS = 10.0
# Sentinel status for transport-level probe failures (DNS, TLS, timeout): no
# HTTP response was received, so no real status code applies.
PROBE_NETWORK_FAILURE_STATUS = 0
_CLAUDE_CODE_IDENTITY = "You are Claude Code, Anthropic's official CLI for Claude."
_PROBE_ERROR_MESSAGE_LIMIT = 500

# Upstream error-message fragments that identify a subscription-level refusal:
# the OAuth tokens are valid, but the organization has no active subscription.
SUBSCRIPTION_REFUSED_MARKERS = ("oauth authentication is currently not allowed",)


class ProbeVerdict(str, Enum):
    HEALTHY = "healthy"
    UNSUBSCRIBED = "unsubscribed"
    DISCONNECTED = "disconnected"
    INCONCLUSIVE = "inconclusive"


def classify_probe_result(status: int, message: str | None) -> ProbeVerdict:
    """Map a probe HTTP status + upstream error message to an account verdict.

    Only explicit signals change account state: a 2xx proves the account is
    usable, a 401 proves the credentials are rejected, and a 403 carrying a
    known subscription-refusal marker proves the org is unsubscribed. Anything
    else (400s from contract drift, 403s without a marker, 429s, 5xx, network
    failures) is inconclusive and must not transition the account.
    """
    if 200 <= status < 300:
        return ProbeVerdict.HEALTHY
    if status == 401:
        return ProbeVerdict.DISCONNECTED
    if status == 403 and message:
        lowered = message.lower()
        if any(marker in lowered for marker in SUBSCRIPTION_REFUSED_MARKERS):
            return ProbeVerdict.UNSUBSCRIBED
    return ProbeVerdict.INCONCLUSIVE


async def send_openai_probe(
    *,
    access_token: str,
    chatgpt_account_id: str | None,
    model: str,
) -> int:
    settings = get_settings()
    base = settings.upstream_base_url.rstrip("/")
    if "/backend-api" not in base:
        base = f"{base}/backend-api"
    url = f"{base}/codex/responses"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "text/event-stream",
        "Content-Type": "application/json",
    }
    if chatgpt_account_id and not chatgpt_account_id.startswith(("email_", "local_")):
        headers["chatgpt-account-id"] = chatgpt_account_id
    body = {
        "model": model,
        "instructions": "Respond with a single dot.",
        "input": [
            {
                "role": "user",
                "content": [{"type": "input_text", "text": "."}],
            }
        ],
        # The codex/responses upstream rejects ``max_output_tokens``
        # ("Unsupported parameter") — the probe must not send it.
        "stream": True,
        "store": False,
    }
    timeout = aiohttp.ClientTimeout(
        total=PROBE_REQUEST_TIMEOUT_SECONDS,
        sock_connect=PROBE_CONNECT_TIMEOUT_SECONDS,
    )
    try:
        async with lease_http_session() as session:
            async with session.post(url, headers=headers, json=body, timeout=timeout) as resp:
                # Initiating the request is enough to wake the upstream
                # rate-limiter; we do not consume the SSE body.
                return resp.status
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        logger.warning(
            "Probe upstream request failed account=%s error=%s",
            chatgpt_account_id,
            exc,
        )
        return PROBE_NETWORK_FAILURE_STATUS


async def send_messages_probe(
    *,
    access_token: str,
    base_url: str,
    model: str,
) -> tuple[int, str | None]:
    settings = get_settings()
    url = urljoin(base_url.rstrip("/") + "/", "v1/messages")
    headers = {
        "Authorization": f"Bearer {access_token}",
        "anthropic-version": settings.anthropic_version,
        "anthropic-beta": ANTHROPIC_OAUTH_BETA,
        "content-type": "application/json",
        "accept": "application/json",
    }
    body = {
        "model": model,
        "max_tokens": 4,
        "system": [{"type": "text", "text": _CLAUDE_CODE_IDENTITY}],
        "messages": [{"role": "user", "content": "Reply OK only."}],
        "stream": False,
    }
    timeout = aiohttp.ClientTimeout(
        total=PROBE_REQUEST_TIMEOUT_SECONDS,
        connect=PROBE_CONNECT_TIMEOUT_SECONDS,
    )
    try:
        async with lease_http_session() as session:
            async with session.post(url, headers=headers, json=body, timeout=timeout) as resp:
                if resp.status >= 400:
                    return resp.status, await read_probe_error(resp)
                return resp.status, None
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        logger.warning("Anthropic subscription check failed error=%s", exc)
        return PROBE_NETWORK_FAILURE_STATUS, str(exc)


async def read_probe_error(resp: aiohttp.ClientResponse) -> str:
    raw = await resp.read()
    text = raw.decode("utf-8", errors="replace").strip() if raw else ""
    if not text:
        return f"Subscription check returned HTTP {resp.status}"
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        message = text
    else:
        error = parsed.get("error") if isinstance(parsed, dict) else None
        if isinstance(error, dict):
            message_value = error.get("message") or error.get("type")
            message = str(message_value) if message_value is not None else text
        else:
            message = text
    if len(message) > _PROBE_ERROR_MESSAGE_LIMIT:
        return message[: _PROBE_ERROR_MESSAGE_LIMIT - 1] + "..."
    return message
