from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urljoin

import aiohttp

from app.core.anthropic.models import AnthropicUsage, merge_usage_values
from app.core.anthropic.oauth import ANTHROPIC_OAUTH_BETA
from app.core.anthropic.parsing import parse_sse_event
from app.core.clients.http import lease_http_session
from app.core.config.settings import get_settings

_PRIMER_CHUNK_SIZE = 8192
_PRIMER_CONNECT_TIMEOUT_SECONDS = 5.0
_PRIMER_TOTAL_TIMEOUT_SECONDS = 30.0
_DEFAULT_ANTHROPIC_PRIMER_PROMPT = "Reply with OK only."
_ERROR_MESSAGE_LIMIT = 1000

# Anthropic OAuth (Bearer) credentials are only honored when the first system
# block is exactly the Claude Code identity line. Real traffic carries it via the
# inbound Claude Code client; a synthetic primer must supply it itself or the
# upstream rejects the request with 401.
_CLAUDE_CODE_IDENTITY = "You are Claude Code, Anthropic's official CLI for Claude."


@dataclass(frozen=True, slots=True)
class AnthropicPrimerResult:
    success: bool
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    error_code: str | None = None
    error_message: str | None = None


async def send_anthropic_primer(
    access_token: str,
    *,
    model: str,
    prompt: str,
    request_id: str,
    warmup_header: str,
) -> AnthropicPrimerResult:
    """Send a minimal Messages primer to anchor an Anthropic account's usage window.

    Mirrors the authentication that ``AnthropicProxyService`` uses for real
    traffic (Bearer access token + ``anthropic-version``) and adds the OAuth beta
    header that the proxy normally relies on the inbound Claude Code client to
    supply. Success is defined as the upstream stream completing with a 2xx.
    """
    settings = get_settings()
    url = urljoin(settings.anthropic_upstream_base_url.rstrip("/") + "/", "v1/messages")
    headers = {
        "Authorization": f"Bearer {access_token}",
        "anthropic-version": settings.anthropic_version,
        "anthropic-beta": ANTHROPIC_OAUTH_BETA,
        "content-type": "application/json",
        "accept": "text/event-stream",
        "x-request-id": request_id,
        warmup_header: "1",
    }
    body = {
        "model": model,
        "max_tokens": 4,
        "system": [{"type": "text", "text": _CLAUDE_CODE_IDENTITY}],
        "messages": [{"role": "user", "content": prompt or _DEFAULT_ANTHROPIC_PRIMER_PROMPT}],
        "stream": True,
    }
    timeout = aiohttp.ClientTimeout(
        total=_PRIMER_TOTAL_TIMEOUT_SECONDS,
        connect=_PRIMER_CONNECT_TIMEOUT_SECONDS,
    )

    usage: AnthropicUsage | None = None
    text_buffer = ""
    async with lease_http_session() as session:
        async with session.post(url, json=body, headers=headers, timeout=timeout) as resp:
            if resp.status >= 400:
                return AnthropicPrimerResult(
                    success=False,
                    error_code=f"upstream_{resp.status}",
                    error_message=await _read_primer_error(resp),
                )
            async for chunk in resp.content.iter_chunked(_PRIMER_CHUNK_SIZE):
                if not chunk:
                    continue
                text_buffer, usage = _collect_usage(text_buffer, bytes(chunk), usage)

    return AnthropicPrimerResult(
        success=True,
        input_tokens=usage.input_tokens if usage is not None else None,
        output_tokens=usage.output_tokens if usage is not None else None,
        cached_input_tokens=usage.cache_read_input_tokens if usage is not None else None,
    )


def _collect_usage(
    text_buffer: str,
    chunk: bytes,
    usage: AnthropicUsage | None,
) -> tuple[str, AnthropicUsage | None]:
    text_buffer += chunk.decode("utf-8", errors="replace")
    normalized = text_buffer.replace("\r\n", "\n")
    while "\n\n" in normalized:
        block, normalized = normalized.split("\n\n", 1)
        event = parse_sse_event(block)
        if event is None:
            continue
        event_usage = getattr(event, "usage", None)
        if event_usage is None:
            message = getattr(event, "message", None)
            if message is not None:
                event_usage = getattr(message, "usage", None)
        usage = merge_usage_values(usage, event_usage)
    return normalized, usage


async def _read_primer_error(resp: aiohttp.ClientResponse) -> str:
    raw = await resp.read()
    text = raw.decode("utf-8", errors="replace").strip() if raw else ""
    if not text:
        return f"Anthropic warm-up primer returned HTTP {resp.status}"
    if len(text) > _ERROR_MESSAGE_LIMIT:
        return text[: _ERROR_MESSAGE_LIMIT - 1] + "..."
    return text
